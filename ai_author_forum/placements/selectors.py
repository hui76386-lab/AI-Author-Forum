from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import Journal, JournalStatus

from .models import JournalUserPreference
from .services import PLACEABLE_REVIEW_STATUSES


def _page(queryset, *, page=1, page_size=20, maximum=100):
    try:
        page_size = int(page_size or 20)
    except (TypeError, ValueError):
        page_size = 20
    page_size = min(max(page_size, 1), maximum)
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    return page_obj, paginator


def select_journals(*, user, query="", scope="all", page=1, page_size=20):
    """Return a server-paginated, user-scoped journal selector queryset."""
    queryset = Journal.objects.filter(status=JournalStatus.ACTIVE).order_by(
        "name", "slug"
    )
    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(name_cn__icontains=query)
            | Q(slug__icontains=query)
        )
    if scope == "favorites":
        queryset = queryset.filter(
            user_preferences__user=user, user_preferences__is_favorite=True
        )
        queryset = queryset.order_by("name", "slug")
    elif scope == "recent":
        queryset = queryset.filter(
            user_preferences__user=user, user_preferences__last_used_at__isnull=False
        )
        queryset = queryset.order_by("-user_preferences__last_used_at", "name", "slug")
    else:
        queryset = queryset.order_by("name", "slug")
    return _page(queryset, page=page, page_size=page_size)


def journal_payload(journal, *, user):
    preference = JournalUserPreference.objects.filter(
        user=user, journal=journal
    ).first()
    return {
        "id": journal.pk,
        "name": journal.name,
        "name_cn": journal.name_cn,
        "slug": journal.slug,
        "status": journal.status,
        "is_favorite": bool(preference and preference.is_favorite),
        "last_used_at": (
            preference.last_used_at.isoformat()
            if preference and preference.last_used_at
            else None
        ),
        "usage_count": preference.usage_count if preference else 0,
    }


def select_articles(*, query="", journal_slug="", page=1, page_size=20):
    """Return eligible articles, prioritising items that are not placed yet.

    ``journal_slug`` is kept as the public parameter name for compatibility
    with the existing URLs, but it accepts a journal slug, English name, or
    Chinese name. Articles related to the selected journal are included as
    well as articles whose primary journal is the selected journal.
    """
    queryset = (
        ArticlePage.objects.filter(
            review_status__in=PLACEABLE_REVIEW_STATUSES,
            primary_journal__status=JournalStatus.ACTIVE,
        )
        .select_related("primary_journal")
        .annotate(
            current_placement_count=Count(
                "placements",
                filter=Q(placements__is_active=True),
                distinct=True,
            )
        )
        .order_by("current_placement_count", "title", "pk")
    )
    query = (query or "").strip()
    if query:
        for term in query.split():
            queryset = queryset.filter(
                Q(title__icontains=term)
                | Q(static_slug__icontains=term)
                | Q(abstract__icontains=term)
                | Q(authors__icontains=term)
                | Q(keywords__icontains=term)
            )
    journal_query = (journal_slug or "").strip()
    if journal_query:
        matching_journals = Journal.objects.filter(
            status=JournalStatus.ACTIVE
        ).filter(
            Q(slug__iexact=journal_query)
            | Q(name__icontains=journal_query)
            | Q(name_cn__icontains=journal_query)
        )
        queryset = queryset.filter(
            Q(primary_journal__in=matching_journals)
            | Q(related_journals__in=matching_journals)
        ).distinct()
    return _page(queryset, page=page, page_size=page_size)


def article_payload(article):
    return {
        "id": article.pk,
        "title": article.title,
        "slug": article.static_slug,
        "journal": article.primary_journal.slug if article.primary_journal_id else "",
        "journal_name": (
            str(article.primary_journal) if article.primary_journal_id else ""
        ),
        "review_status": article.review_status,
        "is_live": article.live,
        "placement_count": getattr(article, "current_placement_count", 0),
    }


def mark_journal_used(*, user, journal):
    preference, _ = JournalUserPreference.objects.get_or_create(
        user=user, journal=journal
    )
    preference.last_used_at = timezone.now()
    preference.usage_count += 1
    preference.save(update_fields=("last_used_at", "usage_count"))
    return preference
