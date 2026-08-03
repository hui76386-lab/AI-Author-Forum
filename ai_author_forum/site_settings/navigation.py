from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from wagtail.models import Site

from ai_author_forum.journals.models import (
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.models import (
    AuditAction,
    AuditLog,
    AuditStatus,
    ColumnEmptyBehavior,
    ContentColumnConfig,
    NavigationArea,
    NavigationEntryStatus,
    NavigationGroup,
    NavigationItem,
    NavigationItemPathRedirect,
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
    NavigationTargetType,
)

DEFAULT_TEMPLATE_NAME = "Default journal navigation template"
DEFAULT_MAIN_NAME = "Main site navigation"

CONTENT_COLUMN_DEFAULTS = {
    "ai-article": {
        "template_variant": "research_list",
        "show_open_access_badge": True,
        "enable_type_filter": True,
    },
    "news": {
        "template_variant": "news_landing",
        "enable_type_filter": False,
    },
    "opinion": {
        "template_variant": "chronological",
        "enable_type_filter": False,
    },
    "research-analysis": {
        "template_variant": "chronological",
        "enable_type_filter": False,
    },
    "research-articles": {
        "template_variant": "research_list",
        "enable_type_filter": True,
    },
    "news-and-comment": {
        "template_variant": "news_landing",
        "enable_type_filter": False,
    },
}

EDITORIAL_PAGE_TITLES = {
    "careers": "Careers",
    "books-and-culture": "Books & Culture",
    "podcasts": "Podcasts",
    "videos": "Videos",
}

LEGACY_CORE_COLUMN_PATHS = {
    "ai-article": "/explore-content/ai-article/",
    "news": "/explore-content/news/",
    "opinion": "/explore-content/opinion/",
    "research-analysis": "/explore-content/research-analysis/",
}

DEFAULT_JOURNAL_GROUPS = (
    {
        "label": "Explore content",
        "code": "explore-content",
        "items": (
            (
                "Research articles",
                "research-articles",
                NavigationTargetType.CONTENT_COLUMN,
            ),
            ("News & Comment", "news-and-comment", NavigationTargetType.CONTENT_COLUMN),
            ("Current issue", "current-issue", NavigationTargetType.CURRENT_ISSUE),
            ("Browse issues", "browse-issues", NavigationTargetType.ISSUE_ARCHIVE),
        ),
    },
    {
        "label": "About this journal",
        "code": "about-this-journal",
        "items": (
            (
                "Journal information",
                "journal-information",
                NavigationTargetType.INTERNAL_PATH,
            ),
            ("Contact", "contact", NavigationTargetType.INTERNAL_PATH),
        ),
    },
    {
        "label": "Publish with us",
        "code": "publish-with-us",
        "items": (
            (
                "Author guidelines",
                "author-guidelines",
                NavigationTargetType.INTERNAL_PATH,
            ),
        ),
    },
)

DEFAULT_MAIN_GROUPS = (
    {
        "label": "Explore content",
        "code": "explore-content",
        "items": (
            (
                "AI Article",
                "ai-article",
                NavigationTargetType.CONTENT_COLUMN,
            ),
            (
                "News",
                "news",
                NavigationTargetType.CONTENT_COLUMN,
            ),
            (
                "Opinion",
                "opinion",
                NavigationTargetType.CONTENT_COLUMN,
            ),
            (
                "Research Analysis",
                "research-analysis",
                NavigationTargetType.CONTENT_COLUMN,
            ),
            (
                "Careers",
                "careers",
                NavigationTargetType.INTERNAL_PATH,
                "/explore-content/careers/",
            ),
            (
                "Books & Culture",
                "books-and-culture",
                NavigationTargetType.INTERNAL_PATH,
                "/explore-content/books-and-culture/",
            ),
            (
                "Podcasts",
                "podcasts",
                NavigationTargetType.INTERNAL_PATH,
                "/explore-content/podcasts/",
            ),
            (
                "Videos",
                "videos",
                NavigationTargetType.INTERNAL_PATH,
                "/explore-content/videos/",
            ),
            (
                "Current issue",
                "current-issue",
                NavigationTargetType.CURRENT_ISSUE,
            ),
            (
                "Browse issues",
                "browse-issues",
                NavigationTargetType.ISSUE_ARCHIVE,
            ),
        ),
    },
    {
        "label": "Journals",
        "code": "journals",
        "items": (
            (
                "A-Z journals",
                "a-z-journals",
                NavigationTargetType.INTERNAL_PATH,
                "/journals/",
            ),
        ),
    },
    {
        "label": "About the forum",
        "code": "about-the-forum",
        "items": (
            (
                "Forum Staff",
                "forum-staff",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/forum-staff/",
            ),
            (
                "About the Editors",
                "about-the-editors",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/about-the-editors/",
            ),
            (
                "Research Cross-Forum Editorial Team",
                "research-cross-forum-editorial-team",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/research-cross-forum-editorial-team/",
            ),
            (
                "Forum Information",
                "forum-information",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/forum-information/",
            ),
            (
                "Forum Metrics",
                "forum-metrics",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/forum-metrics/",
            ),
            (
                "Our publishing models",
                "our-publishing-models",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/our-publishing-models/",
            ),
            (
                "Editorial Values Statement",
                "editorial-values-statement",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/editorial-values-statement/",
            ),
            (
                "Editorial policies",
                "editorial-policies",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/editorial-policies/",
            ),
            (
                "Journalistic Principles",
                "journalistic-principles",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/journalistic-principles/",
            ),
            (
                "Development of the Forum",
                "development-of-the-forum",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/development-of-the-forum/",
            ),
            (
                "Awards",
                "awards",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/awards/",
            ),
            (
                "Contact",
                "contact",
                NavigationTargetType.INTERNAL_PATH,
                "/about-the-forum/contact/",
            ),
        ),
    },
    {
        "label": "Co authoring with AI",
        "code": "co-authoring-with-ai",
        "items": (
            (
                "Definition of a co author to the AI",
                "definition-of-a-co-author-to-the-ai",
                NavigationTargetType.INTERNAL_PATH,
                "/co-authoring-with-ai/definition-of-a-co-author-to-the-ai/",
            ),
            (
                "Responsibility of the Co author",
                "responsibility-of-the-co-author",
                NavigationTargetType.INTERNAL_PATH,
                "/co-authoring-with-ai/responsibility-of-the-co-author/",
            ),
        ),
    },
    {
        "label": "For readers",
        "code": "for-readers",
        "items": (
            (
                "How AI authored Articles produced",
                "how-ai-authored-articles-produced",
                NavigationTargetType.INTERNAL_PATH,
                "/for-readers/how-ai-authored-articles-produced/",
            ),
            (
                "Readers responsibility",
                "readers-responsibility",
                NavigationTargetType.INTERNAL_PATH,
                "/for-readers/readers-responsibility/",
            ),
        ),
    },
)


@dataclass(frozen=True)
class NavigationImpact:
    scope: str
    journal_id: int | None
    paths: tuple[str, ...]


def default_site() -> Site:
    site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
    if site is None:
        raise ValidationError(
            "A Wagtail Site is required before navigation can be configured."
        )
    return site


def _record(action, actor, target, message, metadata=None, status=AuditStatus.SUCCESS):
    return AuditLog.record(
        action=action,
        status=status,
        actor=actor,
        target=target,
        message=message,
        metadata=metadata or {},
    )


def navigation_audit_metadata(
    nav_set,
    *,
    before=None,
    after=None,
    affected_page_count=None,
    publish_job_id=None,
    failure_reason="",
    **extra,
):
    if affected_page_count is None:
        affected_page_count = len(navigation_change_impact(nav_set).paths)
    return {
        "scope": nav_set.scope,
        "journal_id": nav_set.journal_id,
        "navigation_version": nav_set.version,
        "before": before or {},
        "after": after or {},
        "affected_page_count": affected_page_count,
        "publish_job_id": publish_job_id,
        "failure_reason": failure_reason,
        **extra,
    }


def ensure_main_navigation_set(*, site=None, actor=None) -> NavigationSet:
    site = site or default_site()
    nav_set = NavigationSet.objects.filter(
        site=site,
        scope=NavigationScope.MAIN_SITE,
        status=NavigationSetStatus.ACTIVE,
        is_template=False,
        journal__isnull=True,
    ).first()
    if nav_set:
        return nav_set
    with transaction.atomic():
        nav_set = NavigationSet.objects.create(
            site=site,
            scope=NavigationScope.MAIN_SITE,
            name=DEFAULT_MAIN_NAME,
            status=NavigationSetStatus.ACTIVE,
        )
        _create_groups(nav_set, DEFAULT_MAIN_GROUPS, site=site)
        _attach_main_editorial_pages(nav_set, site=site)
        for code, old_path in LEGACY_CORE_COLUMN_PATHS.items():
            item = NavigationItem.objects.get(group__navigation_set=nav_set, code=code)
            NavigationItemPathRedirect.objects.get_or_create(
                old_path=old_path,
                defaults={
                    "navigation_item": item,
                    "new_path": item.target_url,
                    "http_status": 301,
                    "is_active": True,
                },
            )
        _record(
            AuditAction.CONFIGURE,
            actor,
            nav_set,
            "Created main-site managed navigation baseline.",
            {"scope": nav_set.scope, "version": nav_set.version},
        )
    return nav_set


def ensure_default_journal_navigation_template(
    *, site=None, actor=None
) -> NavigationSet:
    site = site or default_site()
    nav_set = NavigationSet.objects.filter(
        site=site,
        is_template=True,
        name=DEFAULT_TEMPLATE_NAME,
    ).first()
    if nav_set:
        return nav_set
    with transaction.atomic():
        nav_set = NavigationSet.objects.create(
            site=site,
            scope=NavigationScope.JOURNAL,
            name=DEFAULT_TEMPLATE_NAME,
            status=NavigationSetStatus.ACTIVE,
            is_template=True,
        )
        _create_groups(nav_set, DEFAULT_JOURNAL_GROUPS, site=site, template=True)
        _record(
            AuditAction.CONFIGURE,
            actor,
            nav_set,
            "Created default journal navigation template.",
            {"scope": "template", "version": nav_set.version},
        )
    return nav_set


def ensure_navigation_for_journal(journal, *, site=None, actor=None) -> NavigationSet:
    existing = NavigationSet.objects.filter(
        journal=journal,
        scope=NavigationScope.JOURNAL,
        status=NavigationSetStatus.ACTIVE,
        is_template=False,
    ).first()
    if existing:
        return existing
    template = ensure_default_journal_navigation_template(site=site, actor=actor)
    return copy_template_to_journal(template=template, journal=journal, actor=actor)


def copy_template_to_journal(
    *, template: NavigationSet, journal, actor=None, overwrite=False
) -> NavigationSet:
    if not template.is_template:
        raise ValidationError(
            "Only a default navigation template can be copied to a journal."
        )
    with transaction.atomic():
        existing_qs = NavigationSet.objects.select_for_update().filter(
            journal=journal,
            scope=NavigationScope.JOURNAL,
            status=NavigationSetStatus.ACTIVE,
            is_template=False,
        )
        existing = existing_qs.first()
        existing_before = (
            {"navigation_set_id": existing.pk, "status": existing.status}
            if existing
            else {}
        )
        if existing and not overwrite:
            return existing
        if existing and overwrite:
            existing.status = NavigationSetStatus.ARCHIVED
            existing.save(update_fields=["status", "updated_at"])
        nav_set = NavigationSet.objects.create(
            site=template.site,
            scope=NavigationScope.JOURNAL,
            journal=journal,
            name=f"{journal.name} navigation",
            status=NavigationSetStatus.ACTIVE,
            copied_from_template=template,
        )
        for group in template.groups.order_by("sort_order", "pk"):
            new_group = NavigationGroup.objects.create(
                navigation_set=nav_set,
                label=group.label,
                code=group.code,
                sort_order=group.sort_order,
                is_visible=group.is_visible,
                status=group.status,
            )
            for item in group.items.order_by("sort_order", "pk"):
                new_item = NavigationItem.objects.create(
                    site=template.site,
                    area=item.area,
                    label=item.label,
                    slug=item.slug,
                    group=new_group,
                    code=item.managed_code,
                    target_type=item.target_type,
                    internal_path=_journalize_internal_path(
                        item.internal_path, journal
                    ),
                    external_url=item.external_url,
                    open_in_new_tab=item.open_in_new_tab,
                    sort_order=item.sort_order,
                    is_active=item.is_active,
                    is_visible=item.is_visible,
                    status=item.status,
                    allow_direct_access=item.allow_direct_access,
                    is_core=False,
                )
                if item.target_type == NavigationTargetType.CONTENT_COLUMN:
                    ContentColumnConfig.objects.create(
                        navigation_item=new_item,
                        **_content_column_defaults(new_item.managed_code),
                    )
        _record(
            AuditAction.CONFIGURE,
            actor,
            nav_set,
            "Copied default navigation template to journal.",
            navigation_audit_metadata(
                nav_set,
                before=existing_before,
                after={
                    "navigation_set_id": nav_set.pk,
                    "status": nav_set.status,
                },
                template_id=template.pk,
                overwrite=overwrite,
            ),
        )
    return nav_set


def _create_groups(nav_set, groups: Iterable[dict], *, site, template=False):
    for group_order, group_data in enumerate(groups, start=1):
        group = NavigationGroup.objects.create(
            navigation_set=nav_set,
            label=group_data["label"],
            code=group_data["code"],
            sort_order=group_order,
        )
        for item_order, raw in enumerate(group_data["items"], start=1):
            label, code, target_type, *rest = raw
            internal_path = rest[0] if rest else ""
            if target_type == NavigationTargetType.INTERNAL_PATH and not internal_path:
                internal_path = f"/{code}/"
            item = NavigationItem.objects.create(
                site=site,
                area=(
                    NavigationArea.JOURNALS
                    if nav_set.scope == NavigationScope.JOURNAL
                    else NavigationArea.HOME
                ),
                label=label,
                slug=code,
                group=group,
                code=code,
                target_type=target_type,
                internal_path=internal_path,
                sort_order=item_order,
                is_core=not template,
            )
            if target_type == NavigationTargetType.CONTENT_COLUMN:
                ContentColumnConfig.objects.create(
                    navigation_item=item,
                    **_content_column_defaults(item.managed_code),
                )


def _content_column_defaults(code):
    configured = CONTENT_COLUMN_DEFAULTS.get(code, {})
    return {
        "template_variant": configured.get("template_variant", "chronological"),
        "default_sort": "published_desc",
        "minimum_publish_items": 1,
        "empty_behavior": "block_publish",
        "show_open_access_badge": configured.get("show_open_access_badge", False),
        "show_authors": True,
        "show_abstract": True,
        "enable_type_filter": configured.get("enable_type_filter", False),
        "enable_year_filter": True,
        "page_size": 20,
    }


def _attach_main_editorial_pages(nav_set, *, site):
    from ai_author_forum.standardpages.models import IndexPage, StandardPage

    root = site.root_page.specific
    explore = root.get_children().filter(slug="explore-content").specific().first()
    if explore is None:
        explore = IndexPage(
            title="Explore content",
            slug="explore-content",
            introduction="Editorial content and publication issue navigation.",
            body=[],
            live=True,
            show_in_menus=False,
        )
        root.add_child(instance=explore)
        explore.save_revision().publish()
    for code, title in EDITORIAL_PAGE_TITLES.items():
        page = explore.get_children().filter(slug=code).specific().first()
        if page is None:
            page = StandardPage(
                title=title,
                slug=code,
                introduction=f"{title} content is maintained by authorised editors in Wagtail.",
                body=[],
                live=True,
                show_in_menus=False,
            )
            explore.add_child(instance=page)
            page.save_revision().publish()
        NavigationItem.objects.filter(
            group__navigation_set=nav_set,
            code=code,
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path=f"/explore-content/{code}/",
        ).update(
            target_type=NavigationTargetType.WAGTAIL_PAGE,
            page_id=page.pk,
            internal_path="",
            external_url="",
            url="",
        )


def _journalize_internal_path(path: str, journal) -> str:
    if not path:
        return ""
    if path.startswith("/journals/"):
        return path
    return f"/journals/{journal.slug}{path if path.startswith('/') else '/' + path}"


def get_active_navigation_set(
    *, journal=None, site=None, strict=False
) -> NavigationSet | None:
    site = site or default_site()
    qs = NavigationSet.objects.filter(
        site=site,
        status=NavigationSetStatus.ACTIVE,
        is_template=False,
    )
    if journal is not None:
        nav_set = qs.filter(scope=NavigationScope.JOURNAL, journal=journal).first()
        if strict and nav_set is None:
            raise ValidationError(f"Journal '{journal}' has no active navigation set.")
        return nav_set
    nav_set = qs.filter(scope=NavigationScope.MAIN_SITE, journal__isnull=True).first()
    if strict and nav_set is None:
        raise ValidationError("Main site has no active navigation set.")
    return nav_set


def get_navigation_context(*, journal=None, site=None, current_path="", strict=False):
    nav_set = get_active_navigation_set(journal=journal, site=site, strict=strict)
    if nav_set is None:
        return {
            "navigation_set": None,
            "groups": (),
            "scope": "journal" if journal else "main_site",
        }

    groups_qs = (
        nav_set.groups.filter(is_visible=True, status=NavigationEntryStatus.ACTIVE)
        .prefetch_related("items", "items__page", "items__content_column_config")
        .order_by("sort_order", "pk")
    )
    groups = list(groups_qs)
    candidate_items = [
        item
        for group in groups
        for item in group.items.all()
        if item.is_active
        and item.is_visible
        and item.status == NavigationEntryStatus.ACTIVE
    ]

    column_slugs = [
        item.placement_target_slug
        for item in candidate_items
        if item.target_type == NavigationTargetType.CONTENT_COLUMN
    ]
    placement_counts = {}
    if column_slugs:
        rows = (
            ArticlePlacement.objects.available()
            .filter(
                target_type=ArticlePlacement.TargetType.SECTION,
                target_slug__in=column_slugs,
                slot__code__in=(
                    "column_featured",
                    "column_secondary",
                    "column_list",
                    "column_sidebar",
                ),
            )
            .values("target_slug")
            .annotate(item_count=models.Count("article_id", distinct=True))
        )
        placement_counts = {row["target_slug"]: row["item_count"] for row in rows}

    issue_scope = (
        PublicationIssueScope.JOURNAL
        if journal is not None
        else PublicationIssueScope.MAIN_SITE
    )
    issue_qs = PublicationIssue.objects.filter(
        scope=issue_scope,
        journal=journal,
        status=PublicationIssueStatus.PUBLISHED,
    )
    has_current_issue = issue_qs.filter(is_current=True).exists()
    has_issue_archive = issue_qs.exists()

    def is_ready_for_navigation(item):
        if item.target_type == NavigationTargetType.WAGTAIL_PAGE:
            return bool(item.page_id and item.page.live)
        if item.target_type == NavigationTargetType.CURRENT_ISSUE:
            return has_current_issue
        if item.target_type == NavigationTargetType.ISSUE_ARCHIVE:
            return has_issue_archive
        if item.target_type != NavigationTargetType.CONTENT_COLUMN:
            return bool(item.target_url)
        try:
            config = item.content_column_config
        except ContentColumnConfig.DoesNotExist as error:
            if strict:
                raise ValidationError(
                    f"Content column navigation item {item.pk} has no ContentColumnConfig."
                ) from error
            return False
        if config.empty_behavior != ColumnEmptyBehavior.HIDE_NAVIGATION:
            return bool(item.target_url)
        return (
            placement_counts.get(item.placement_target_slug, 0)
            >= config.minimum_publish_items
        )

    result_groups = []
    for group in groups:
        items = []
        for item in group.items.all():
            if (
                not item.is_active
                or not item.is_visible
                or item.status != NavigationEntryStatus.ACTIVE
                or not is_ready_for_navigation(item)
            ):
                continue
            url = item.target_url
            items.append(
                {
                    "label": item.label,
                    "code": item.managed_code,
                    "target_type": item.target_type,
                    "url": url,
                    "open_in_new_tab": item.open_in_new_tab,
                    "rel": (
                        "noopener noreferrer"
                        if item.open_in_new_tab
                        and item.target_type == NavigationTargetType.EXTERNAL_URL
                        else ""
                    ),
                    "is_active": bool(
                        url and current_path and current_path.startswith(url)
                    ),
                }
            )
        if not items:
            continue
        result_groups.append(
            {
                "label": group.label,
                "code": group.code,
                "items": tuple(items),
                "is_active": any(item["is_active"] for item in items),
            }
        )
    return {
        "navigation_set": nav_set,
        "groups": tuple(result_groups),
        "scope": nav_set.scope,
        "journal": journal,
    }


def reorder_groups(nav_set, ordered_group_ids, *, expected_version=None, actor=None):
    if expected_version is not None and nav_set.version != expected_version:
        raise ValidationError("Navigation set version changed; reload before sorting.")
    owned = set(nav_set.groups.values_list("pk", flat=True))
    if set(ordered_group_ids) != owned:
        raise ValidationError(
            "Groups can only be reordered inside their current navigation set."
        )
    with transaction.atomic():
        before = list(
            nav_set.groups.order_by("sort_order", "pk").values_list("pk", flat=True)
        )
        for idx, group_id in enumerate(ordered_group_ids, start=1):
            NavigationGroup.objects.filter(pk=group_id, navigation_set=nav_set).update(
                sort_order=idx
            )
        nav_set.bump_version(user=actor)
        _record(
            AuditAction.CONFIGURE,
            actor,
            nav_set,
            "Reordered navigation groups.",
            {
                "before": before,
                "after": list(ordered_group_ids),
                "version": nav_set.version,
            },
        )


def move_items(group, ordered_item_ids, *, expected_version=None, actor=None):
    nav_set = group.navigation_set
    if expected_version is not None and nav_set.version != expected_version:
        raise ValidationError("Navigation set version changed; reload before sorting.")
    ordered_item_ids = list(ordered_item_ids)
    if len(ordered_item_ids) != len(set(ordered_item_ids)):
        raise ValidationError("Navigation item order contains duplicates.")
    owned = set(
        NavigationItem.objects.filter(group__navigation_set=nav_set).values_list(
            "pk", flat=True
        )
    )
    if not set(ordered_item_ids).issubset(owned):
        raise ValidationError(
            "Items can only move inside their current navigation set."
        )
    with transaction.atomic():
        before = list(
            NavigationItem.objects.filter(pk__in=ordered_item_ids).values(
                "pk", "group_id", "sort_order"
            )
        )
        for idx, item_id in enumerate(ordered_item_ids, start=1):
            NavigationItem.objects.filter(
                pk=item_id, group__navigation_set=nav_set
            ).update(
                group=group,
                sort_order=idx,
            )
        nav_set.bump_version(user=actor)
        _record(
            AuditAction.CONFIGURE,
            actor,
            nav_set,
            "Reordered or moved navigation items.",
            {
                "destination_group_id": group.pk,
                "before": before,
                "after": ordered_item_ids,
                "version": nav_set.version,
            },
        )


def reorder_navigation_tree(
    nav_set, *, ordered_group_ids, items_by_group, expected_version=None, actor=None
):
    ordered_group_ids = [int(group_id) for group_id in ordered_group_ids]
    normalized_items = {
        int(group_id): [int(item_id) for item_id in item_ids]
        for group_id, item_ids in items_by_group.items()
    }
    if expected_version is not None and nav_set.version != int(expected_version):
        raise ValidationError(
            f"Navigation configuration changed concurrently (expected {expected_version}, current {nav_set.version})."
        )
    current_group_ids = set(nav_set.groups.values_list("pk", flat=True))
    if set(ordered_group_ids) != current_group_ids or len(ordered_group_ids) != len(
        current_group_ids
    ):
        raise ValidationError("Group ordering must contain every group exactly once.")
    if set(normalized_items) != current_group_ids:
        raise ValidationError("Item ordering must provide one list for every group.")
    all_item_ids = [
        item_id for values in normalized_items.values() for item_id in values
    ]
    current_item_ids = set(
        NavigationItem.objects.filter(group__navigation_set=nav_set).values_list(
            "pk", flat=True
        )
    )
    if (
        len(all_item_ids) != len(set(all_item_ids))
        or set(all_item_ids) != current_item_ids
    ):
        raise ValidationError(
            "Item ordering must contain every item in the current navigation set exactly once."
        )
    before = {
        "groups": list(
            nav_set.groups.order_by("sort_order", "pk").values_list("pk", flat=True)
        ),
        "items": {
            str(group.pk): list(
                group.items.order_by("sort_order", "pk").values_list("pk", flat=True)
            )
            for group in nav_set.groups.all()
        },
    }
    with transaction.atomic():
        for order, group_id in enumerate(ordered_group_ids, start=1):
            NavigationGroup.objects.filter(pk=group_id, navigation_set=nav_set).update(
                sort_order=order
            )
        for group_id, item_ids in normalized_items.items():
            for order, item_id in enumerate(item_ids, start=1):
                NavigationItem.objects.filter(
                    pk=item_id, group__navigation_set=nav_set
                ).update(group_id=group_id, sort_order=order)
        nav_set.bump_version(user=actor)
        _record(
            AuditAction.CONFIGURE,
            actor,
            nav_set,
            "Reordered navigation groups and items.",
            navigation_audit_metadata(
                nav_set,
                before=before,
                after={
                    "groups": ordered_group_ids,
                    "items": {
                        str(key): value for key, value in normalized_items.items()
                    },
                },
            ),
        )
    return nav_set


def _item_snapshot(item):
    return {
        "label": item.label,
        "code": item.managed_code,
        "target_type": item.target_type,
        "target_url": item.target_url,
        "group_id": item.group_id,
        "sort_order": item.sort_order,
        "status": item.status,
        "is_visible": item.is_visible,
        "allow_direct_access": item.allow_direct_access,
    }


def _group_snapshot(group):
    return {
        "label": group.label,
        "code": group.code,
        "sort_order": group.sort_order,
        "status": group.status,
        "is_visible": group.is_visible,
    }


def record_navigation_group_change(group, *, actor=None, before=None, created=False):
    group.navigation_set.bump_version(user=actor)
    _record(
        AuditAction.CONFIGURE,
        actor,
        group,
        "Created navigation group." if created else "Updated navigation group.",
        navigation_audit_metadata(
            group.navigation_set,
            before=before,
            after=_group_snapshot(group),
        ),
    )
    return group


def record_navigation_item_change(item, *, actor=None, before=None, created=False):
    nav_set = item.group.navigation_set
    # NavigationItem.save already increments the set version for managed entries.
    nav_set.refresh_from_db(fields=["version", "updated_at", "updated_by"])
    if actor is not None and getattr(actor, "is_authenticated", False):
        NavigationSet.objects.filter(pk=nav_set.pk).update(updated_by=actor)
        nav_set.updated_by = actor
    _record(
        AuditAction.CONFIGURE,
        actor,
        item,
        "Created navigation item." if created else "Updated navigation item.",
        navigation_audit_metadata(
            nav_set,
            before=before,
            after=_item_snapshot(item),
        ),
    )
    return item


def duplicate_navigation_item(item, *, actor=None):
    nav_set = item.group.navigation_set
    base = f"{item.managed_code}-copy"
    code = base
    suffix = 2
    while NavigationItem.objects.filter(
        group__navigation_set=nav_set, code=code
    ).exists():
        code = f"{base}-{suffix}"
        suffix += 1
    duplicate = NavigationItem.objects.create(
        site=item.site,
        area=item.area,
        label=f"{item.label} copy",
        slug=code,
        group=item.group,
        code=code,
        page=item.page,
        target_type=item.target_type,
        category=item.category,
        internal_path=item.internal_path,
        external_url=item.external_url,
        open_in_new_tab=item.open_in_new_tab,
        is_visible=False,
        status=NavigationEntryStatus.DRAFT,
        allow_direct_access=item.allow_direct_access,
        sort_order=(
            item.group.items.aggregate(max_order=models.Max("sort_order"))["max_order"]
            or 0
        )
        + 1,
        is_active=True,
        is_core=item.is_core,
        updated_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    if item.target_type == NavigationTargetType.CONTENT_COLUMN:
        try:
            source = item.content_column_config
        except ContentColumnConfig.DoesNotExist:
            ContentColumnConfig.objects.create(navigation_item=duplicate)
        else:
            ContentColumnConfig.objects.create(
                navigation_item=duplicate,
                intro=source.intro,
                cover_image=source.cover_image,
                category=source.category,
                enable_type_filter=source.enable_type_filter,
                enable_year_filter=source.enable_year_filter,
                page_size=source.page_size,
                seo_title=source.seo_title,
                seo_description=source.seo_description,
                empty_message=source.empty_message,
            )
    nav_set.refresh_from_db(fields=["version"])
    _record(
        AuditAction.CONFIGURE,
        actor,
        duplicate,
        "Copied navigation item as an unpublished draft.",
        navigation_audit_metadata(
            nav_set,
            before=_item_snapshot(item),
            after=_item_snapshot(duplicate),
            source_item_id=item.pk,
        ),
    )
    return duplicate


def set_navigation_group_visibility(group, *, visible, actor=None):
    before = _group_snapshot(group)
    group.is_visible = bool(visible)
    group.status = (
        NavigationEntryStatus.ACTIVE if visible else NavigationEntryStatus.HIDDEN
    )
    group.save(update_fields=["is_visible", "status", "updated_at"])
    group.navigation_set.bump_version(user=actor)
    _record(
        AuditAction.CONFIGURE,
        actor,
        group,
        "Enabled navigation group." if visible else "Hidden navigation group.",
        navigation_audit_metadata(
            group.navigation_set,
            before=before,
            after=_group_snapshot(group),
        ),
    )
    return group


def set_navigation_item_visibility(item, *, visible, actor=None):
    if visible and item.status == NavigationEntryStatus.ARCHIVED:
        raise ValidationError(
            "Restore the archived navigation item before enabling it."
        )
    before = _item_snapshot(item)
    item.is_visible = bool(visible)
    item.status = (
        NavigationEntryStatus.ACTIVE if visible else NavigationEntryStatus.HIDDEN
    )
    item.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    item.save(update_fields=["is_visible", "status", "updated_by", "updated_at"])
    item.group.navigation_set.refresh_from_db(fields=["version"])
    _record(
        AuditAction.CONFIGURE,
        actor,
        item,
        "Enabled navigation item." if visible else "Hidden navigation item.",
        navigation_audit_metadata(
            item.group.navigation_set,
            before=before,
            after=_item_snapshot(item),
        ),
    )
    return item


def archive_navigation_item(item, *, actor=None):
    if item.status == NavigationEntryStatus.ARCHIVED:
        return item
    before = _item_snapshot(item)
    item.status = NavigationEntryStatus.ARCHIVED
    item.is_visible = False
    item.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    item.save(update_fields=["status", "is_visible", "updated_by", "updated_at"])
    item.group.navigation_set.refresh_from_db(fields=["version"])
    _record(
        AuditAction.CONFIGURE,
        actor,
        item,
        "Archived navigation item.",
        navigation_audit_metadata(
            item.group.navigation_set,
            before=before,
            after=_item_snapshot(item),
        ),
    )
    return item


def restore_navigation_item(item, *, actor=None):
    if item.status != NavigationEntryStatus.ARCHIVED:
        return item
    before = _item_snapshot(item)
    item.status = NavigationEntryStatus.ACTIVE
    item.is_visible = True
    item.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    item.save(update_fields=["status", "is_visible", "updated_by", "updated_at"])
    item.group.navigation_set.refresh_from_db(fields=["version"])
    _record(
        AuditAction.CONFIGURE,
        actor,
        item,
        "Restored navigation item.",
        navigation_audit_metadata(
            item.group.navigation_set,
            before=before,
            after=_item_snapshot(item),
        ),
    )
    return item


def navigation_item_reference_counts(item):
    from ai_author_forum.static_publish.models import StaticManifest

    navigation_references = (
        NavigationItem.objects.exclude(pk=item.pk)
        .filter(internal_path=item.target_url)
        .count()
    )
    placements = ArticlePlacement.objects.filter(
        target_type=ArticlePlacement.TargetType.SECTION,
        target_slug=item.placement_target_slug,
    )
    active_placements = placements.filter(is_active=True).count()
    static_pages = 0
    historical_versions = 0
    for manifest in StaticManifest.objects.all().only("metadata"):
        targets = (manifest.metadata or {}).get("targets", [])
        matched = [
            target
            for target in targets
            if target.get("target_id", "").startswith(f"navigation_item:{item.pk}")
            or target.get("canonical_path") == item.target_url
            or item.pk
            in (target.get("dependencies") or {}).get("navigation_item_ids", [])
        ]
        if matched:
            historical_versions += 1
            static_pages += len(matched)
    return {
        "article_categories": 1 if item.category_id else 0,
        "active_placements": active_placements,
        "placements": placements.count(),
        "static_pages": static_pages,
        "navigation_references": navigation_references,
        "historical_versions": historical_versions,
        "redirects": item.path_redirects.count(),
    }


def hard_delete_navigation_item(item, *, actor=None):
    assert_can_hard_delete_navigation_item(item, user=actor)
    nav_set = item.group.navigation_set
    before = _item_snapshot(item)
    target_id = str(item.pk)
    target_label = str(item)
    item.delete()
    nav_set.bump_version(user=actor)
    AuditLog.record(
        action=AuditAction.CONFIGURE,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target_type="NavigationItem",
        target_id=target_id,
        target_label=target_label,
        message="Hard deleted unpublished navigation item.",
        metadata=navigation_audit_metadata(
            nav_set,
            before=before,
            after={},
        ),
    )
    return True


def assert_can_hard_delete_navigation_item(item, *, user=None):
    if not (
        getattr(user, "is_superuser", False)
        or getattr(user, "has_perm", lambda perm: False)(
            "site_settings.delete_navigation_objects"
        )
    ):
        raise PermissionDenied(
            "Hard deletion requires high-risk navigation delete permission."
        )
    blockers = []
    if item.status != NavigationEntryStatus.DRAFT:
        blockers.append("item has been active or archived")
    if item.category_id:
        blockers.append("article category reference")
    if ArticlePlacement.objects.filter(
        target_type=ArticlePlacement.TargetType.SECTION,
        target_slug=item.placement_target_slug,
    ).exists():
        blockers.append("active or historic placements")
    if item.path_redirects.exists():
        blockers.append("historical path redirects")
    if (
        NavigationItem.objects.exclude(pk=item.pk)
        .filter(internal_path=item.target_url)
        .exists()
    ):
        blockers.append("other navigation references")
    from ai_author_forum.static_publish.models import StaticManifest

    for manifest in StaticManifest.objects.all().only("metadata"):
        targets = (manifest.metadata or {}).get("targets", [])
        if any(
            target.get("target_id", "").startswith(f"navigation_item:{item.pk}")
            or target.get("canonical_path") == item.target_url
            or item.pk
            in (target.get("dependencies") or {}).get("navigation_item_ids", [])
            for target in targets
        ):
            blockers.append("static manifest references")
            break
    if blockers:
        raise ValidationError({"delete": blockers})
    return True


def navigation_change_impact(nav_set: NavigationSet) -> NavigationImpact:
    from django.conf import settings
    from wagtail.models import Page

    from ai_author_forum.articles.models import ArticlePage
    from ai_author_forum.articles.services import get_approved_articles
    from ai_author_forum.journals.models import JournalCategory, JournalCategoryStatus
    from ai_author_forum.static_publish.providers import output_path_for_url

    paths = set()

    def add_content_columns():
        items = (
            NavigationItem.objects.filter(
                group__navigation_set=nav_set,
                target_type=NavigationTargetType.CONTENT_COLUMN,
                status__in=(NavigationEntryStatus.ACTIVE, NavigationEntryStatus.HIDDEN),
            )
            .select_related("content_column_config")
            .order_by("pk")
        )
        for item in items:
            try:
                config = item.content_column_config
            except ContentColumnConfig.DoesNotExist as exc:
                raise ValidationError(
                    f"Content column navigation item {item.pk} has no ContentColumnConfig"
                ) from exc
            placements = ArticlePlacement.objects.available().filter(
                slot__code="column_list",
                target_type=ArticlePlacement.TargetType.SECTION,
                target_slug=item.placement_target_slug,
            )
            page_size = max(1, int(config.page_size or 20))
            page_count = max(1, (placements.count() + page_size - 1) // page_size)
            paths.add(item.target_url)
            for page_number in range(2, page_count + 1):
                paths.add(f"{item.target_url}page/{page_number}/")

    add_content_columns()
    page_ids = NavigationItem.objects.filter(
        group__navigation_set=nav_set,
        target_type=NavigationTargetType.WAGTAIL_PAGE,
        page__isnull=False,
    ).values_list("page_id", flat=True)
    for page_url in Page.objects.filter(pk__in=page_ids, live=True).values_list(
        "url_path", flat=True
    ):
        page = Page.objects.filter(url_path=page_url).first()
        if page and page.url:
            paths.add(page.url)

    if nav_set.journal_id:
        journal = nav_set.journal
        paths.update(
            {
                f"/journals/{journal.slug}/",
                f"/journals/{journal.slug}/current-issue/",
                f"/journals/{journal.slug}/issues/",
            }
        )
        for static_slug in (
            get_approved_articles()
            .filter(primary_journal=journal)
            .values_list("static_slug", flat=True)
        ):
            paths.add(f"/articles/{static_slug}/")
        category_page_size = max(
            1, int(getattr(settings, "STATIC_CATEGORY_PAGE_SIZE", 20))
        )
        categories = JournalCategory.objects.filter(
            journal=journal,
            status__in=(JournalCategoryStatus.ACTIVE, JournalCategoryStatus.HIDDEN),
            generate_static_page=True,
        )
        for category in categories:
            canonical = category.get_absolute_url()
            paths.add(canonical)
            placement_count = (
                ArticlePlacement.objects.available()
                .filter(
                    target_type=ArticlePlacement.TargetType.CATEGORY,
                    target_category=category,
                    source=ArticlePlacement.Source.SYSTEM,
                    placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
                )
                .count()
            )
            for page_number in range(
                2,
                max(1, (placement_count + category_page_size - 1) // category_page_size)
                + 1,
            ):
                paths.add(f"{canonical}page/{page_number}/")
        return NavigationImpact(
            scope=nav_set.scope,
            journal_id=journal.pk,
            paths=tuple(sorted(paths, key=output_path_for_url)),
        )

    paths.update({"/", "/journals/", "/search/"})
    # Generic Wagtail pages and fixed main-site sections all render the main header.
    for page in Page.objects.live().public().specific().order_by("path"):
        if page.depth == 1 or isinstance(page, ArticlePage):
            continue
        if page.url:
            paths.add(page.url)
    from ai_author_forum.static_publish.frontend import get_static_sections

    paths.update(
        f"/explore-content/{section['slug']}/" for section in get_static_sections()
    )
    return NavigationImpact(
        scope=nav_set.scope,
        journal_id=None,
        paths=tuple(sorted(paths, key=output_path_for_url)),
    )
