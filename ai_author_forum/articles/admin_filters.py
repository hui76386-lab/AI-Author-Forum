from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db import OperationalError, ProgrammingError
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import ArticlePage, ArticleReviewRecord

ORDERING_CHOICES = (
    ("-updated", "最近更新"),
    ("updated", "最早更新"),
    ("title", "标题 A-Z"),
    ("-title", "标题 Z-A"),
    ("review_status", "审核状态"),
    ("publication_status", "交付状态"),
)
ORDERING_MAP = {
    "-updated": ("-latest_revision_created_at", "-pk"),
    "updated": ("latest_revision_created_at", "pk"),
    "title": ("title", "pk"),
    "-title": ("-title", "-pk"),
    "review_status": ("review_status", "-latest_revision_created_at", "-pk"),
    "publication_status": (
        "publication_status",
        "-latest_revision_created_at",
        "-pk",
    ),
}
PAGE_SIZES = (25, 50, 100)


@dataclass(frozen=True)
class ArticleAdminFilters:
    q: str = ""
    review_status: str = ""
    publication_status: str = ""
    article_type: str = ""
    primary_journal: str = ""
    category: str = ""
    updated_from: str = ""
    updated_to: str = ""
    ordering: str = "-updated"
    page_size: int = 25
    submitter: str = ""
    waiting: str = ""
    owner: str = ""
    reviewed_by: str = ""
    reviewed_from: str = ""

    @classmethod
    def from_querydict(cls, params, *, pending_only=False):
        ordering = params.get("ordering", "-updated")
        if ordering not in ORDERING_MAP:
            ordering = "-updated"
        try:
            page_size = int(params.get("page_size", 25))
        except (TypeError, ValueError):
            page_size = 25
        if page_size not in PAGE_SIZES:
            page_size = 25
        return cls(
            q=params.get("q", "").strip(),
            review_status=(
                ArticlePage.ReviewStatus.SUBMITTED
                if pending_only
                else params.get("review_status", "")
            ),
            publication_status=params.get("publication_status", ""),
            article_type=params.get("article_type", ""),
            primary_journal=params.get("primary_journal", ""),
            category=params.get("category", ""),
            updated_from=params.get("updated_from", ""),
            updated_to=params.get("updated_to", ""),
            ordering=ordering,
            page_size=page_size,
            submitter=params.get("submitter", "").strip(),
            waiting=params.get("waiting", ""),
            owner=params.get("owner", ""),
            reviewed_by=params.get("reviewed_by", ""),
            reviewed_from=params.get("reviewed_from", ""),
        )

    def as_dict(self):
        return self.__dict__.copy()


def build_article_admin_queryset(filters: ArticleAdminFilters):
    submissions = ArticleReviewRecord.objects.filter(
        article_id=OuterRef("pk"),
        action=ArticleReviewRecord.Action.SUBMIT,
    ).order_by("-created_at", "-pk")
    queryset = (
        ArticlePage.objects.select_related(
            "primary_journal",
            "featured_image",
            "latest_revision",
            "latest_revision__user",
        )
        .prefetch_related("category_assignments__category")
        .annotate(
            submitted_at=Subquery(submissions.values("created_at")[:1]),
            submitted_by_id=Subquery(submissions.values("reviewer_id")[:1]),
            submitted_by_username=Subquery(
                submissions.values("reviewer__username")[:1]
            ),
        )
    )

    if filters.q:
        queryset = _apply_search(queryset, filters.q)
    if filters.review_status in ArticlePage.ReviewStatus.values:
        queryset = queryset.filter(review_status=filters.review_status)
    if filters.publication_status in ArticlePage.PublicationStatus.values:
        queryset = queryset.filter(publication_status=filters.publication_status)
    if filters.article_type in ArticlePage.ArticleType.values:
        queryset = queryset.filter(article_type=filters.article_type)

    journal_id = _positive_int(filters.primary_journal)
    if journal_id:
        queryset = queryset.filter(primary_journal_id=journal_id)
    category_id = _positive_int(filters.category)
    if category_id:
        queryset = queryset.filter(category_assignments__category_id=category_id)

    updated_from = parse_date(filters.updated_from)
    if updated_from:
        queryset = queryset.filter(
            latest_revision_created_at__gte=timezone.make_aware(
                datetime.combine(updated_from, time.min)
            )
        )
    updated_to = parse_date(filters.updated_to)
    if updated_to:
        queryset = queryset.filter(
            latest_revision_created_at__lte=timezone.make_aware(
                datetime.combine(updated_to, time.max)
            )
        )

    if filters.submitter:
        queryset = queryset.filter(
            Q(submitted_by_username__icontains=filters.submitter)
            | Q(latest_revision__user__username__icontains=filters.submitter)
        )

    owner_id = _positive_int(filters.owner)
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)

    reviewed_by_id = _positive_int(filters.reviewed_by)
    reviewed_from = parse_date(filters.reviewed_from)
    if reviewed_by_id or reviewed_from:
        review_filters = Q(
            review_records__action__in=(
                ArticleReviewRecord.Action.INITIAL_APPROVE,
                ArticleReviewRecord.Action.INITIAL_RETURN,
                ArticleReviewRecord.Action.INITIAL_REJECT,
                ArticleReviewRecord.Action.FINAL_APPROVE,
                ArticleReviewRecord.Action.FINAL_RETURN,
                ArticleReviewRecord.Action.FINAL_REJECT,
            )
        )
        if reviewed_by_id:
            review_filters &= Q(review_records__reviewer_id=reviewed_by_id)
        if reviewed_from:
            review_filters &= Q(review_records__created_at__date__gte=reviewed_from)
        queryset = queryset.filter(review_filters)

    queryset = _apply_waiting_filter(queryset, filters.waiting)

    return queryset.distinct().order_by(*ORDERING_MAP[filters.ordering])


def _apply_search(queryset, query):
    try:
        search_ids = [
            result.pk
            for result in ArticlePage.objects.search(query, order_by_relevance=False)[
                :1000
            ]
        ]
    except (OperationalError, ProgrammingError, NotImplementedError):
        search_ids = []
    if search_ids:
        return queryset.filter(pk__in=search_ids)
    return queryset.filter(
        Q(title__icontains=query)
        | Q(abstract__icontains=query)
        | Q(body__icontains=query)
        | Q(authors__icontains=query)
        | Q(keywords__icontains=query)
    )


def _apply_waiting_filter(queryset, waiting):
    now = timezone.now()
    thresholds = {
        "24h": now - timedelta(hours=24),
        "48h": now - timedelta(hours=48),
        "3d": now - timedelta(days=3),
        "7d": now - timedelta(days=7),
    }
    if waiting in thresholds:
        return queryset.filter(submitted_at__lte=thresholds[waiting])
    return queryset


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
