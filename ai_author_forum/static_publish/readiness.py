from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone

from ai_author_forum.articles.display import resolve_article_image
from ai_author_forum.articles.integrations import get_site_settings
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.models import (
    ColumnEmptyBehavior,
    ContentColumnConfig,
    NavigationEntryStatus,
    NavigationItem,
    NavigationSetStatus,
    NavigationTargetType,
)

CORE_COLUMN_CODES = {"ai-article", "news", "opinion", "research-analysis"}
PUBLIC_REVIEW_STATUSES = {
    ArticlePage.ReviewStatus.APPROVED,
    ArticlePage.ReviewStatus.PUBLISHED,
}
READINESS_TARGET_TYPES = {
    "wagtail_page",
    "journal_index",
    "static_info_page",
    "article_page",
    "search_page",
    "journal_page",
    "category_page",
    "managed_content_column",
    "main_current_issue",
    "main_issue_archive",
    "journal_current_issue",
    "journal_issue_archive",
    "issue_detail",
}


@dataclass(frozen=True)
class ReadinessFinding:
    code: str
    message: str
    target_type: str = ""
    target_id: str = ""
    path: str = ""


@dataclass
class ContentReadinessResult:
    configured: bool = False
    blockers: list[ReadinessFinding] = field(default_factory=list)
    warnings: list[ReadinessFinding] = field(default_factory=list)
    checked_navigation_items: int = 0
    checked_columns: int = 0
    checked_placements: int = 0
    checked_issues: int = 0
    checked_static_targets: int = 0

    @property
    def is_ready(self):
        return self.configured and not self.blockers

    def block(self, code, message, *, target=None, path=""):
        self.blockers.append(_finding(code, message, target=target, path=path))

    def warn(self, code, message, *, target=None, path=""):
        self.warnings.append(_finding(code, message, target=target, path=path))

    def to_dict(self):
        return {
            "configured": self.configured,
            "is_ready": self.is_ready,
            "checked_navigation_items": self.checked_navigation_items,
            "checked_columns": self.checked_columns,
            "checked_placements": self.checked_placements,
            "checked_issues": self.checked_issues,
            "checked_static_targets": self.checked_static_targets,
            "blockers": [asdict(item) for item in self.blockers],
            "warnings": [asdict(item) for item in self.warnings],
        }


def _finding(code, message, *, target=None, path=""):
    return ReadinessFinding(
        code=code,
        message=message,
        target_type=(target._meta.label_lower if target is not None else ""),
        target_id=(str(target.pk) if target is not None and target.pk else ""),
        path=path,
    )


def _check_active_journal_chiefs(result, *, at):
    for journal in Journal.objects.filter(status=JournalStatus.ACTIVE).order_by("pk"):
        chief_count = (
            JournalEditorAssignment.objects.effective(at=at)
            .filter(
                journal=journal,
                role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            )
            .count()
        )
        if chief_count != 1:
            result.block(
                "active_journal_chief_invalid",
                (
                    f"Active journal '{journal}' requires exactly one effective "
                    f"chief editor; found {chief_count}."
                ),
                target=journal,
                path=f"/journals/{journal.slug}/",
            )


def _normalise_public_path(value):
    path = urlsplit(value or "").path
    if not path:
        return ""
    if path == "/":
        return path
    return f"/{path.strip('/')}/"


def requires_content_readiness(targets):
    return bool(
        getattr(settings, "STATIC_PUBLISH_ENFORCE_CONTENT_READINESS", False)
        and any(
            getattr(target, "target_type", "") in READINESS_TARGET_TYPES
            for target in targets
        )
    )


def requires_homepage_readiness(targets):
    return any(
        _normalise_public_path(
            getattr(target, "canonical_path", "") or getattr(target, "url", "")
        )
        == "/"
        for target in targets
    )


def _homepage_placements(at):
    return list(
        ArticlePlacement.objects.available_for_static_release(at=at)
        .filter(
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
            target_slug="",
            slot__code__in=(
                "home_hero",
                "home_visual_stories",
                "home_featured",
                "latest_ai_article",
            ),
        )
        .select_related(
            "slot",
            "article",
            "article__primary_journal",
            "article__featured_image",
            "override_image",
        )
        .order_by("slot__sort_order", "-is_pinned", "sort_order", "pk")
    )


def _has_valid_homepage_alt(placement, visual):
    article = placement.article
    image = visual.image
    return bool(
        (placement.override_image_alt or "").strip()
        or (article.featured_image_alt or "").strip()
        or (getattr(image, "description", "") or "").strip()
        or (getattr(image, "title", "") or "").strip()
    )


def _check_homepage(result, *, at):
    path = "/"
    placements = _homepage_placements(at)
    result.checked_placements += len(placements)
    by_slot = {}
    for placement in placements:
        by_slot.setdefault(placement.slot.code, []).append(placement)

    required_counts = {"home_hero": 1, "home_visual_stories": 2}
    for slot_code, required in required_counts.items():
        count = len(by_slot.get(slot_code, []))
        if count != required:
            result.block(
                f"{slot_code}_count_invalid",
                f"Homepage slot '{slot_code}' requires exactly {required} effective placement(s); found {count}.",
                path=path,
            )

    seen_articles = {}
    site_settings = get_site_settings()
    for placement in placements:
        article = placement.article
        previous = seen_articles.get(article.pk)
        if previous is not None:
            result.block(
                "homepage_article_duplicate",
                f"Article '{article.title}' appears in both '{previous.slot.code}' and '{placement.slot.code}'.",
                target=placement,
                path=path,
            )
        else:
            seen_articles[article.pk] = placement

        if article.review_status not in PUBLIC_REVIEW_STATUSES:
            result.block(
                "homepage_article_not_approved",
                f"Homepage placement {placement.pk} references an unapproved article.",
                target=placement,
                path=path,
            )
        if (
            not article.live
            or not ArticlePage.objects.live().public().filter(pk=article.pk).exists()
        ):
            result.block(
                "homepage_article_not_public",
                f"Homepage article '{article.title}' is not live and public.",
                target=article,
                path=path,
            )
        if not (article.static_slug or "").strip():
            result.block(
                "homepage_article_static_path_missing",
                f"Homepage article '{article.title}' has no fixed static slug.",
                target=article,
                path=path,
            )

        visual = resolve_article_image(
            article, placement=placement, site_settings=site_settings
        )
        if visual.is_placeholder or visual.image is None:
            result.block(
                "homepage_placeholder_image",
                f"Homepage placement {placement.pk} resolves to the legacy placeholder image.",
                target=placement,
                path=path,
            )
            continue
        if not _image_exists(visual.image):
            result.block(
                "homepage_image_file_missing",
                f"Homepage placement {placement.pk} points to a missing image file.",
                target=placement,
                path=path,
            )
        if not _has_valid_homepage_alt(placement, visual):
            result.block(
                "homepage_image_alt_missing",
                f"Homepage placement {placement.pk} has no valid image alternative text.",
                target=placement,
                path=path,
            )


def check_homepage_readiness(*, at=None):
    result = ContentReadinessResult(configured=True)
    _check_homepage(result, at=at or timezone.now())
    return result


def format_blockers(result, *, limit=8):
    messages = [finding.message for finding in result.blockers[:limit]]
    remaining = len(result.blockers) - len(messages)
    if remaining > 0:
        messages.append(f"{remaining} additional blocker(s)")
    return "; ".join(messages)


def _scheduled_section_placements(item, at):
    return list(
        ArticlePlacement.objects.select_related(
            "article",
            "article__primary_journal",
            "article__featured_image",
            "override_image",
            "slot",
        )
        .filter(
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug=item.placement_target_slug,
            is_active=True,
            slot__is_active=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=at))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=at))
        .order_by("slot__code", "-is_pinned", "sort_order", "-created_at", "pk")
    )


def _image_exists(image):
    if image is None or not getattr(image, "file", None) or not image.file.name:
        return False
    try:
        return image.file.storage.exists(image.file.name)
    except (OSError, ValueError):
        return False


def _image_alt(image, explicit=""):
    if image is None:
        return ""
    return (
        (explicit or "").strip()
        or (getattr(image, "description", "") or "").strip()
        or (getattr(image, "title", "") or "").strip()
    )


def _check_image(result, image, *, explicit_alt="", label, target, path=""):
    if image is None:
        return
    if not _image_alt(image, explicit_alt):
        result.block(
            "image_alt_missing",
            f"{label} has no alternative text.",
            target=target,
            path=path,
        )
    if not _image_exists(image):
        result.block(
            "image_file_missing",
            f"{label} points to a missing image file.",
            target=target,
            path=path,
        )


def _published_issue_filter(item, *, current=False):
    nav_set = item.group.navigation_set
    values = {"status": PublicationIssueStatus.PUBLISHED}
    if current:
        values["is_current"] = True
    if nav_set.journal_id:
        values.update(
            scope=PublicationIssueScope.JOURNAL,
            journal_id=nav_set.journal_id,
        )
    else:
        values.update(
            scope=PublicationIssueScope.MAIN_SITE,
            journal__isnull=True,
        )
    return values


def _check_navigation_target(result, item, *, static_paths=None):
    path = _normalise_public_path(item.target_url)
    if not path:
        result.block(
            "navigation_target_missing",
            f"Navigation item '{item.label}' has no resolvable target.",
            target=item,
        )
        return
    if item.target_type == NavigationTargetType.WAGTAIL_PAGE:
        if item.page_id is None or not item.page.live:
            result.block(
                "wagtail_page_not_live",
                f"Navigation page '{item.label}' does not exist or is not live.",
                target=item,
                path=path,
            )
    elif item.target_type == NavigationTargetType.CURRENT_ISSUE:
        if not PublicationIssue.objects.filter(
            **_published_issue_filter(item, current=True)
        ).exists():
            result.block(
                "current_issue_missing",
                f"Navigation item '{item.label}' has no published current issue.",
                target=item,
                path=path,
            )
    elif item.target_type == NavigationTargetType.ISSUE_ARCHIVE:
        if not PublicationIssue.objects.filter(
            **_published_issue_filter(item)
        ).exists():
            result.block(
                "issue_archive_empty",
                f"Navigation item '{item.label}' has no published issue.",
                target=item,
                path=path,
            )

    if (
        static_paths is not None
        and item.target_type != NavigationTargetType.EXTERNAL_URL
        and path not in static_paths
    ):
        result.block(
            "navigation_static_target_missing",
            f"Navigation target '{item.label}' is not part of this static build.",
            target=item,
            path=path,
        )


def _check_column(result, item, at, *, article_static_paths=None):
    path = _normalise_public_path(item.target_url)
    try:
        config = item.content_column_config
    except ContentColumnConfig.DoesNotExist:
        result.block(
            "column_config_missing",
            f"Content column '{item.label}' has no configuration.",
            target=item,
            path=path,
        )
        return

    result.checked_columns += 1
    try:
        config.full_clean()
    except ValidationError as exc:
        result.block(
            "column_config_invalid",
            f"Content column '{item.label}' is invalid: {exc}",
            target=config,
            path=path,
        )
    _check_image(
        result,
        config.cover_image,
        label=f"Column cover for '{item.label}'",
        target=config,
        path=path,
    )

    placements = _scheduled_section_placements(item, at)
    result.checked_placements += len(placements)
    publishable_article_ids = {
        placement.article_id
        for placement in placements
        if placement.article.review_status in PUBLIC_REVIEW_STATUSES
        and placement.article.primary_journal.status == "active"
    }
    minimum = max(1, int(config.minimum_publish_items or 1))
    if len(publishable_article_ids) < minimum:
        message = (
            f"Content column '{item.label}' has {len(publishable_article_ids)} "
            f"publishable article(s); minimum is {minimum}."
        )
        nav_set = item.group.navigation_set
        is_core = (not nav_set.journal_id) and item.managed_code in CORE_COLUMN_CODES
        if is_core or config.empty_behavior == ColumnEmptyBehavior.BLOCK_PUBLISH:
            result.block("column_minimum_not_met", message, target=config, path=path)
        elif config.empty_behavior == ColumnEmptyBehavior.HIDE_NAVIGATION:
            result.warn(
                "column_hidden_until_minimum",
                f"{message} The public navigation entry will remain hidden.",
                target=config,
                path=path,
            )
        else:
            result.warn("column_minimum_not_met", message, target=config, path=path)

    for placement in placements:
        article = placement.article
        if article.review_status not in PUBLIC_REVIEW_STATUSES:
            result.block(
                "placement_article_not_approved",
                f"Placement {placement.pk} references unapproved article '{article.title}'.",
                target=placement,
                path=path,
            )
        if article.primary_journal.status != "active":
            result.block(
                "placement_journal_inactive",
                f"Placement {placement.pk} references an article in an inactive journal.",
                target=placement,
                path=path,
            )
        article_path = _normalise_public_path(
            f"/articles/{article.static_slug}/" if article.static_slug else ""
        )
        if not article_path:
            result.block(
                "article_static_path_missing",
                f"Article '{article.title}' has no fixed static slug.",
                target=article,
                path=path,
            )
        elif (
            article_static_paths is not None
            and article_path not in article_static_paths
        ):
            result.block(
                "article_static_target_missing",
                f"Article '{article.title}' has no buildable static detail target.",
                target=article,
                path=article_path,
            )
        image = placement.override_image or article.featured_image
        explicit_alt = "" if placement.override_image_id else article.featured_image_alt
        _check_image(
            result,
            image,
            explicit_alt=explicit_alt,
            label=f"Article image for '{article.title}'",
            target=placement,
            path=path,
        )

    invalid_windows = ArticlePlacement.objects.filter(
        target_type=ArticlePlacement.TargetType.SECTION,
        target_slug=item.placement_target_slug,
        is_active=True,
        starts_at__isnull=False,
        ends_at__isnull=False,
        ends_at__lte=F("starts_at"),
    ).only("pk", "starts_at", "ends_at")
    for placement in invalid_windows:
        result.block(
            "placement_window_invalid",
            f"Placement {placement.pk} has an invalid effective time window.",
            target=placement,
            path=path,
        )


def _check_issues(result, *, article_static_paths=None, issue_static_paths=None):
    issues = PublicationIssue.objects.filter(
        status=PublicationIssueStatus.PUBLISHED
    ).select_related("journal", "cover_image")
    for issue in issues:
        result.checked_issues += 1
        path = _normalise_public_path(issue.scope_path)
        if issue_static_paths is not None and path not in issue_static_paths:
            result.block(
                "issue_static_target_missing",
                f"Issue '{issue.title}' has no buildable static detail target.",
                target=issue,
                path=path,
            )
        _check_image(
            result,
            issue.cover_image,
            label=f"Issue cover for '{issue.title}'",
            target=issue,
            path=path,
        )
        assignments = list(
            issue.issue_articles.select_related(
                "article", "article__primary_journal"
            ).prefetch_related("article__related_journals")
        )
        if not assignments:
            result.block(
                "published_issue_empty",
                f"Published issue '{issue.title}' has no approved articles.",
                target=issue,
                path=path,
            )
        for assignment in assignments:
            article = assignment.article
            if article.review_status not in PUBLIC_REVIEW_STATUSES:
                result.block(
                    "issue_article_not_approved",
                    f"Issue '{issue.title}' contains unapproved article '{article.title}'.",
                    target=assignment,
                    path=path,
                )
            if issue.scope == PublicationIssueScope.JOURNAL:
                related_ids = {journal.pk for journal in article.related_journals.all()}
                if (
                    article.primary_journal_id != issue.journal_id
                    and issue.journal_id not in related_ids
                ):
                    result.block(
                        "issue_article_scope_mismatch",
                        f"Article '{article.title}' is outside issue journal scope.",
                        target=assignment,
                        path=path,
                    )
            article_path = _normalise_public_path(
                f"/articles/{article.static_slug}/" if article.static_slug else ""
            )
            if not article_path:
                result.block(
                    "article_static_path_missing",
                    f"Article '{article.title}' has no fixed static slug.",
                    target=article,
                    path=path,
                )
            elif (
                article_static_paths is not None
                and article_path not in article_static_paths
            ):
                result.block(
                    "article_static_target_missing",
                    f"Issue article '{article.title}' has no buildable static detail target.",
                    target=assignment,
                    path=article_path,
                )


def _check_static_targets(result, targets, at):
    static_paths = set()
    article_static_paths = set()
    issue_static_paths = set()
    canonical_owners = {}
    dependency_placement_ids = set()

    for target in targets:
        result.checked_static_targets += 1
        action = getattr(target, "action", "upsert")
        url = _normalise_public_path(getattr(target, "url", ""))
        canonical = _normalise_public_path(
            getattr(target, "canonical_path", "") or getattr(target, "url", "")
        )
        target_type = getattr(target, "target_type", "")
        if action != "delete" and not url:
            result.block(
                "static_target_path_missing",
                f"Static target '{getattr(target, 'source', '')}' has no output URL.",
            )
        if action != "delete" and not canonical:
            result.block(
                "canonical_path_missing",
                f"Static target '{getattr(target, 'source', '')}' has no canonical URL.",
                path=url,
            )
        if url:
            static_paths.add(url)
        if target_type == "article_page" and url:
            article_static_paths.add(url)
        if target_type == "issue_detail" and url:
            issue_static_paths.add(url)
        if action not in {"delete", "redirect"} and canonical:
            previous = canonical_owners.get(canonical)
            if previous is not None and previous != url:
                result.block(
                    "duplicate_canonical_path",
                    f"Static targets '{previous}' and '{url}' share canonical URL '{canonical}'.",
                    path=canonical,
                )
            else:
                canonical_owners[canonical] = url
        dependencies = getattr(target, "dependencies", {}) or {}
        dependency_placement_ids.update(dependencies.get("placement_ids", []))

    if dependency_placement_ids:
        available_ids = set(
            ArticlePlacement.objects.available_for_static_release(at=at)
            .filter(pk__in=dependency_placement_ids)
            .values_list("pk", flat=True)
        )
        for placement_id in sorted(dependency_placement_ids - available_ids):
            result.block(
                "placement_dependency_not_effective",
                f"Static target dependency references unavailable placement {placement_id}.",
            )

    return static_paths, article_static_paths, issue_static_paths


def check_content_readiness(*, targets=None, at=None):
    at = at or timezone.now()
    target_list = list(targets) if targets is not None else None
    result = ContentReadinessResult()
    static_paths = article_static_paths = issue_static_paths = None
    if target_list is not None:
        static_paths, article_static_paths, issue_static_paths = _check_static_targets(
            result, target_list, at
        )
    if target_list is None or requires_homepage_readiness(target_list):
        _check_homepage(result, at=at)
    _check_active_journal_chiefs(result, at=at)

    items = list(
        NavigationItem.objects.select_related(
            "group__navigation_set__journal",
            "page",
            "content_column_config__cover_image",
            "content_column_config__category",
        )
        .filter(
            group__navigation_set__status=NavigationSetStatus.ACTIVE,
            group__navigation_set__is_template=False,
            is_active=True,
        )
        .exclude(status=NavigationEntryStatus.ARCHIVED)
        .order_by("group__navigation_set_id", "group__sort_order", "sort_order", "pk")
    )
    result.configured = bool(items)
    result.checked_navigation_items = len(items)
    if not items:
        result.warn(
            "managed_navigation_missing",
            "No active managed navigation items are configured; production publish is not ready.",
        )
        return result

    for item in items:
        _check_navigation_target(result, item, static_paths=static_paths)
        if item.target_type == NavigationTargetType.CONTENT_COLUMN:
            _check_column(
                result,
                item,
                at,
                article_static_paths=article_static_paths,
            )

    _check_issues(
        result,
        article_static_paths=article_static_paths,
        issue_static_paths=issue_static_paths,
    )
    return result
