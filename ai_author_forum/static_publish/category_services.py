from collections import Counter
from pathlib import Path

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import article_id_from_source
from ai_author_forum.journals.models import (
    JournalCategory,
    JournalCategoryPathRedirect,
    JournalCategoryStatus,
)
from ai_author_forum.placements.category_services import (
    validate_category_placement_consistency,
)
from ai_author_forum.placements.models import ArticlePlacement

CATEGORY_PUBLICATION_DRIFT = "CATEGORY_PUBLICATION_DRIFT"


class CategoryPublicationConsistencyError(RuntimeError):
    code = CATEGORY_PUBLICATION_DRIFT

    def __init__(self, errors):
        self.errors = list(errors)
        summary = "; ".join(
            item.get("message", item.get("code", "drift")) for item in self.errors[:8]
        )
        super().__init__(f"{self.code}: {summary}")


def calculate_category_publish_impact(*, change_set):
    """Return stable old/new category paths supplied by a category change snapshot."""
    before = change_set.get("before") or {}
    after = change_set.get("after") or {}
    return {
        "journal_ids": sorted(
            {
                value
                for value in (before.get("journal_id"), after.get("journal_id"))
                if value
            }
        ),
        "category_ids": sorted(set(change_set.get("category_ids") or ())),
        "old_paths": sorted(set(change_set.get("old_paths") or ())),
        "new_paths": sorted(set(change_set.get("new_paths") or ())),
        "article_ids": sorted(set(change_set.get("article_ids") or ())),
    }


def build_category_page(*, category_id, version_id):
    from .providers import WagtailPageTargetProvider

    prefix = f"category:{category_id}:page:"
    return [
        target
        for target in WagtailPageTargetProvider().get_targets()
        if target.target_id.startswith(prefix)
    ]


def build_category_redirect(*, redirect_id, version_id):
    from .providers import WagtailPageTargetProvider

    target_id = f"category_redirect:{redirect_id}"
    return next(
        (
            target
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_id == target_id
        ),
        None,
    )


def validate_category_publication_consistency(
    *, version_id, targets, staging, journal_ids=None
):
    """Block activation when category assignments, placements, routes, or outputs drift."""
    errors = []
    target_list = list(targets)
    paths = [target.output_path for target in target_list]
    duplicates = sorted(path for path, count in Counter(paths).items() if count > 1)
    if duplicates:
        errors.append(
            {
                "code": "STATIC_OUTPUT_PATH_CONFLICT",
                "message": f"Manifest output paths conflict: {', '.join(duplicates[:10])}",
                "paths": duplicates,
            }
        )

    structured_targets = any(hasattr(target, "target_type") for target in target_list)
    if not structured_targets:
        if errors:
            raise CategoryPublicationConsistencyError(errors)
        return {"version_id": version_id, "status": "valid", "errors": []}

    scoped_journal_ids = (
        {int(value) for value in journal_ids} if journal_ids is not None else None
    )
    category_queryset = JournalCategory.objects.filter(
        status__in=(JournalCategoryStatus.ACTIVE, JournalCategoryStatus.HIDDEN),
        generate_static_page=True,
        journal__status="active",
    )
    if scoped_journal_ids is not None:
        category_queryset = category_queryset.filter(journal_id__in=scoped_journal_ids)
    expected_categories = list(category_queryset.select_related("journal"))
    target_by_id = {getattr(target, "target_id", ""): target for target in target_list}
    for category in expected_categories:
        key = f"category:{category.pk}:page:1"
        target = target_by_id.get(key)
        output = Path(staging, category.get_static_output_path())
        if target is None and not output.is_file():
            errors.append(
                {
                    "code": "CATEGORY_STATIC_OUTPUT_MISSING",
                    "message": f"Category {category.pk} has no generated page-one output.",
                    "category_id": category.pk,
                }
            )

    navigation_queryset = JournalCategory.objects.filter(show_in_navigation=True)
    if scoped_journal_ids is not None:
        navigation_queryset = navigation_queryset.filter(
            journal_id__in=scoped_journal_ids
        )
    invalid_navigation = list(
        navigation_queryset
        .exclude(status=JournalCategoryStatus.ACTIVE, depth__lte=2)
        .values_list("pk", flat=True)
    )
    if invalid_navigation:
        errors.append(
            {
                "code": "CATEGORY_NAVIGATION_INVALID",
                "message": "Navigation contains disabled, archived, hidden, or third-level categories.",
                "category_ids": invalid_navigation,
            }
        )

    article_queryset = ArticlePage.objects.filter(
        live=True,
        review_status__in=(
            ArticlePage.ReviewStatus.APPROVED,
            ArticlePage.ReviewStatus.PUBLISHED,
        ),
        primary_journal__status="active",
        category_assignments__isnull=False,
    )
    if scoped_journal_ids is not None:
        article_queryset = article_queryset.filter(
            primary_journal_id__in=scoped_journal_ids
        )
    article_ids = set(
        article_queryset
        .values_list("pk", flat=True)
        .distinct()
    )
    listed_articles = {}
    for target in target_list:
        dependencies = getattr(target, "dependencies", {}) or {}
        ids = {int(value) for value in dependencies.get("article_ids", ())}
        article_ids.update(ids)
        source_article_id = article_id_from_source(target.source)
        if source_article_id:
            article_ids.add(source_article_id)
        if getattr(target, "target_type", "") == "category_page":
            listed_articles[getattr(target, "target_id", target.source)] = (
                ids,
                {int(value) for value in dependencies.get("placement_ids", ())},
            )

    if article_ids:
        failed_sync = list(
            ArticlePage.objects.filter(
                pk__in=article_ids,
                placement_sync_status=ArticlePage.PlacementSyncStatus.FAILED,
            ).values_list("pk", flat=True)
        )
        if failed_sync:
            errors.append(
                {
                    "code": "CATEGORY_PLACEMENT_SYNC_FAILED",
                    "message": "One or more target articles have failed category placement synchronization.",
                    "article_ids": failed_sync,
                }
            )
        errors.extend(validate_category_placement_consistency(article_ids=article_ids))

    active_system_placement_ids = set(
        ArticlePlacement.objects.filter(
            pk__in={
                placement_id
                for _, placement_ids in listed_articles.values()
                for placement_id in placement_ids
            },
            target_type=ArticlePlacement.TargetType.CATEGORY,
            source=ArticlePlacement.Source.SYSTEM,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
            is_active=True,
        ).values_list("pk", flat=True)
    )
    for target_id, (target_article_ids, placement_ids) in listed_articles.items():
        if target_article_ids and not placement_ids:
            errors.append(
                {
                    "code": "CATEGORY_LISTING_PLACEMENT_MISSING",
                    "message": f"{target_id} lists articles without production placements.",
                    "target_id": target_id,
                }
            )
        elif placement_ids - active_system_placement_ids:
            errors.append(
                {
                    "code": "CATEGORY_LISTING_PLACEMENT_INVALID",
                    "message": f"{target_id} references invalid production placements.",
                    "target_id": target_id,
                    "placement_ids": sorted(
                        placement_ids - active_system_placement_ids
                    ),
                }
            )

    redirect_queryset = JournalCategoryPathRedirect.objects.filter(is_active=True)
    if scoped_journal_ids is not None:
        redirect_queryset = redirect_queryset.filter(
            category__journal_id__in=scoped_journal_ids
        )
    redirects = list(redirect_queryset)
    old_paths = {redirect.old_path for redirect in redirects}
    canonical_paths = {category.get_absolute_url() for category in expected_categories}
    for redirect in redirects:
        if redirect.new_path in old_paths:
            errors.append(
                {
                    "code": "CATEGORY_REDIRECT_CHAIN",
                    "message": f"Redirect {redirect.pk} forms a multi-hop chain.",
                    "redirect_id": redirect.pk,
                }
            )
        if redirect.new_path not in canonical_paths:
            errors.append(
                {
                    "code": "CATEGORY_REDIRECT_TARGET_MISSING",
                    "message": f"Redirect {redirect.pk} does not point to a canonical page in this version.",
                    "redirect_id": redirect.pk,
                }
            )
        target = target_by_id.get(f"category_redirect:{redirect.pk}")
        if (
            target is None
            and not Path(staging, redirect.old_path.strip("/"), "index.html").is_file()
        ):
            errors.append(
                {
                    "code": "CATEGORY_REDIRECT_OUTPUT_MISSING",
                    "message": f"Redirect {redirect.pk} has no static output.",
                    "redirect_id": redirect.pk,
                }
            )

    if errors:
        raise CategoryPublicationConsistencyError(errors)
    return {"version_id": version_id, "status": "valid", "errors": []}
