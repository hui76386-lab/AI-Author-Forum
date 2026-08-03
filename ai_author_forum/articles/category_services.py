from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, When

from ai_author_forum.journals.models import (
    JournalCategory,
    JournalCategoryStatus,
    StaticArticleCategoryAssignment,
)

from .models import ArticleCategoryAssignment, ArticlePage

MAX_RELATED_CATEGORIES = 10


class ArticleCategoryError(ValidationError):
    def __init__(self, code, message, *, field_name=None, context=None):
        self.code = code
        self.field_name = field_name
        self.context = context or {}
        super().__init__(message, code=code, params=self.context)


@dataclass(frozen=True)
class LiveArticleCategories:
    primary: JournalCategory | None
    related: tuple[JournalCategory, ...]

    @property
    def all(self):
        return ((self.primary,) if self.primary else ()) + self.related


def validate_article_category_revision(
    *, article, revision_content=None, action="submit"
):
    assignments = _assignment_payload(article, revision_content)
    if action in {"draft", "save_draft", "preview"}:
        return assignments
    primary = [item for item in assignments if item["is_primary"]]
    related = [item for item in assignments if not item["is_primary"]]
    if not primary:
        raise ArticleCategoryError(
            "ARTICLE_PRIMARY_CATEGORY_REQUIRED",
            "Exactly one primary category is required before review.",
            field_name="category_assignments",
        )
    if len(primary) > 1:
        raise ArticleCategoryError(
            "ARTICLE_MULTIPLE_PRIMARY_CATEGORIES",
            "Only one primary category is allowed.",
            field_name="category_assignments",
        )
    if len(related) > MAX_RELATED_CATEGORIES:
        raise ArticleCategoryError(
            "ARTICLE_TOO_MANY_RELATED_CATEGORIES",
            "At most 10 related categories are allowed.",
            field_name="category_assignments",
        )
    ids = [item["category_id"] for item in assignments]
    if len(ids) != len(set(ids)):
        raise ArticleCategoryError(
            "ARTICLE_DUPLICATE_CATEGORY",
            "The same category cannot be both primary and related.",
            field_name="category_assignments",
        )
    categories = {
        category.pk: category
        for category in JournalCategory.objects.filter(pk__in=ids).select_related(
            "journal"
        )
    }
    if len(categories) != len(ids):
        raise ArticleCategoryError(
            "CATEGORY_NOT_FOUND", "One or more categories do not exist."
        )
    for category_id in ids:
        category = categories[category_id]
        if category.journal_id != article.primary_journal_id:
            raise ArticleCategoryError(
                "CATEGORY_CROSS_JOURNAL",
                "Selected category does not belong to the article primary journal.",
                context={
                    "article_id": article.pk,
                    "journal_id": article.primary_journal_id,
                    "category_id": category.pk,
                },
            )
        if category.status not in {
            JournalCategoryStatus.ACTIVE,
            JournalCategoryStatus.HIDDEN,
        }:
            raise ArticleCategoryError(
                "CATEGORY_INACTIVE",
                "Disabled or archived categories must be migrated before review.",
                context={"category_id": category.pk, "status": category.status},
            )
    return assignments


def copy_static_article_categories(
    *, static_article, article_page, actor=None, request_id="", create_revision=True
):
    source = list(
        static_article.category_assignments.select_related("category").order_by(
            "sort_order", "pk"
        )
    )
    payload = [
        {"category_id": item.category_id, "is_primary": item.is_primary}
        for item in source
    ]
    validate_article_category_revision(
        article=article_page,
        revision_content={"category_assignments": payload},
        action="submit" if payload else "draft",
    )
    with transaction.atomic():
        article_page.category_assignments.all().delete()
        for item in source:
            ArticleCategoryAssignment.objects.create(
                article=article_page,
                category=item.category,
                is_primary=item.is_primary,
                sort_order=item.sort_order,
            )
        revision = None
        if create_revision:
            revision = article_page.save_revision(
                user=actor,
                changed=True,
                bypass_article_permission_check=True,
            )
        from ai_author_forum.site_settings.models import AuditAction, AuditStatus
        from ai_author_forum.site_settings.services import record_audit_event

        record_audit_event(
            action=AuditAction.IMPORT,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=article_page,
            request_id=request_id,
            message="Static article category assignments copied to article draft",
            metadata={
                "operation": "copy_static_article_categories",
                "static_article_id": static_article.pk,
                "article_id": article_page.pk,
                "revision_id": revision.pk if revision else None,
                "category_ids": [item.category_id for item in source],
            },
        )
        return revision


def get_live_article_categories(*, article_id):
    article = ArticlePage.objects.select_related("live_revision").get(pk=article_id)
    source = article.live_revision.as_object() if article.live_revision_id else article
    assignments = list(
        source.category_assignments.select_related(
            "category", "category__journal"
        ).order_by("sort_order", "pk")
    )
    primary = next((item.category for item in assignments if item.is_primary), None)
    related = tuple(item.category for item in assignments if not item.is_primary)
    return LiveArticleCategories(primary=primary, related=related)


def get_articles_for_category(
    *,
    category,
    include_descendants=None,
    publication_time=None,
    page=1,
    page_size=20,
):
    from ai_author_forum.placements.models import ArticlePlacement

    if category.status in {
        JournalCategoryStatus.DISABLED,
        JournalCategoryStatus.ARCHIVED,
    }:
        return Paginator(ArticlePage.objects.none(), page_size).get_page(page)
    include_descendants = (
        category.aggregate_descendants
        if include_descendants is None
        else include_descendants
    )
    category_ids = (
        category.get_descendant_ids(include_self=True)
        if include_descendants
        else [category.pk]
    )
    placements = (
        ArticlePlacement.objects.available(at=publication_time)
        .filter(
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category_id__in=category_ids,
            source=ArticlePlacement.Source.SYSTEM,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
        )
        .select_related("article", "target_category")
        .annotate(
            direct_priority=Case(
                When(target_category_id=category.pk, then=0),
                default=1,
                output_field=IntegerField(),
            ),
            primary_priority=Case(
                When(metadata__category_assignment_role="primary", then=0),
                default=1,
                output_field=IntegerField(),
            ),
        )
        .order_by(
            "article_id",
            "direct_priority",
            "primary_priority",
            "pk",
        )
    )
    chosen = {}
    for placement in placements:
        chosen.setdefault(placement.article_id, placement)
    article_ids = list(chosen)
    articles = (
        ArticlePage.objects.filter(pk__in=article_ids)
        .select_related("primary_journal")
        .order_by("-first_published_at", "-pk")
    )
    return Paginator(articles, page_size).get_page(page)


def assign_static_article_categories(*, static_article, primary=None, related=()):
    categories = ([primary] if primary else []) + list(related)
    payload = [
        {"category_id": item.pk, "is_primary": item.pk == getattr(primary, "pk", None)}
        for item in categories
    ]
    shadow = ArticlePage(primary_journal=static_article.journal)
    validate_article_category_revision(
        article=shadow,
        revision_content={"category_assignments": payload},
        action="submit" if payload else "draft",
    )
    with transaction.atomic():
        static_article.category_assignments.all().delete()
        for index, category in enumerate(categories):
            StaticArticleCategoryAssignment.objects.create(
                article=static_article,
                category=category,
                is_primary=category.pk == getattr(primary, "pk", None),
                sort_order=index,
            )


def _assignment_payload(article, revision_content):
    if revision_content is not None:
        raw = revision_content.get("category_assignments", [])
        result = []
        for item in raw:
            category_id = item.get("category_id") or item.get("category")
            if isinstance(category_id, dict):
                category_id = category_id.get("id") or category_id.get("pk")
            if category_id:
                result.append(
                    {
                        "category_id": int(category_id),
                        "is_primary": bool(item.get("is_primary")),
                    }
                )
        return result
    return [
        {"category_id": item.category_id, "is_primary": item.is_primary}
        for item in article.category_assignments.all()
    ]
