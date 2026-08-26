import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import CharField, DateTimeField, Exists, OuterRef, Subquery
from django.db.models.functions import Cast, Coalesce
from wagtail.models import Page, Revision

from ai_author_forum.utils.i18n import article_type_label
from ai_author_forum.utils.public_i18n import (
    localized_article_abstract,
    localized_article_ai_coauthors,
    localized_article_authors,
    localized_article_body,
    localized_article_keywords,
    localized_article_title,
)

from .integrations import get_article_fallback_context
from .models import ArticlePage
from .publication import sync_legacy_article_states

APPROVED_ARTICLE_STATUSES = (
    ArticlePage.ReviewStatus.APPROVED,
    ArticlePage.ReviewStatus.PUBLISHED,
)

IMPORTED_ARTICLE_TYPE_MAP = {
    "ai_article": ArticlePage.ArticleType.AI_ARTICLE,
    "news": ArticlePage.ArticleType.NEWS,
    "opinion": ArticlePage.ArticleType.OPINION,
    "review": ArticlePage.ArticleType.RESEARCH_ANALYSIS,
    "editorial": ArticlePage.ArticleType.RESEARCH_ANALYSIS,
}

IMPORTED_REVIEW_STATUS_MAP = {
    "draft": ArticlePage.ReviewStatus.DRAFT,
    "review": ArticlePage.ReviewStatus.SUBMITTED,
    "approved": ArticlePage.ReviewStatus.APPROVED,
    "placed": ArticlePage.ReviewStatus.APPROVED,
    "built": ArticlePage.ReviewStatus.APPROVED,
    "published": ArticlePage.ReviewStatus.APPROVED,
    "offline": ArticlePage.ReviewStatus.APPROVED,
}

IMPORTED_PUBLICATION_STATUS_MAP = {
    "approved": ArticlePage.PublicationStatus.APPROVED,
    "placed": ArticlePage.PublicationStatus.PLACED,
    "built": ArticlePage.PublicationStatus.BUILT,
    "published": ArticlePage.PublicationStatus.PUBLISHED,
    "offline": ArticlePage.PublicationStatus.OFFLINE,
}


def sync_imported_article(
    static_article, *, source_html="", owner=None, review_status=None
):
    """Create or update the canonical ArticlePage for a legacy import record."""
    with transaction.atomic():
        page = ArticlePage.objects.filter(
            source_static_article_id=static_article.pk,
        ).first()
        static_slug = page.static_slug if page else _unique_static_slug(static_article)
        page_slug = page.slug if page else _unique_page_slug(static_article)
        # Imported content must enter Wagtail moderation as a draft. Import status
        # never grants production approval or bypasses category placement sync.
        imported_publication_status = ""
        review_status = ArticlePage.ReviewStatus.DRAFT
        if source_html:
            body = [("html", _html_body(source_html))]
        elif page is not None:
            body = page.body
        else:
            body = [
                (
                    "paragraph",
                    f"<p>{static_article.abstract or static_article.title}</p>",
                )
            ]
        values = {
            "title": static_article.title,
            "slug": page_slug,
            "abstract": static_article.abstract or static_article.title,
            "body": body,
            "authors": static_article.authors or "Imported author",
            "ai_co_authors": static_article.ai_co_authors,
            "article_type": IMPORTED_ARTICLE_TYPE_MAP.get(
                static_article.article_type,
                ArticlePage.ArticleType.AI_ARTICLE,
            ),
            "primary_journal": static_article.journal,
            "keywords": static_article.keywords or "imported",
            "review_status": review_status,
            "static_slug": static_slug,
            "source_static_article": static_article,
            "live": False,
            "has_unpublished_changes": True,
        }
        if owner is not None or page is None:
            values["owner"] = owner

        values.update(
            publication_status=imported_publication_status,
            build_version=static_article.build_version,
            published_version="",
        )
        if page is None:
            page = ArticlePage(**values)
            Page.get_first_root_node().add_child(instance=page)
        else:
            for field, value in values.items():
                setattr(page, field, value)
            page.save(
                clean=False,
                user=owner,
                bypass_article_permission_check=True,
            )

        from .category_services import copy_static_article_categories

        copy_static_article_categories(
            static_article=static_article,
            article_page=page,
            actor=owner,
            create_revision=False,
        )

        page.save_revision(
            user=owner,
            changed=True,
            bypass_article_permission_check=True,
        )
        sync_legacy_article_states({page.pk})
        return page


def _html_body(source_html):
    match = re.search(
        r"<body[^>]*>(.*?)</body>", source_html or "", re.IGNORECASE | re.DOTALL
    )
    return match.group(1).strip() if match else source_html


def _unique_page_slug(static_article):
    base_slug = static_article.slug or "article"
    if not ArticlePage.objects.filter(slug=base_slug).exists():
        return base_slug
    prefix = static_article.journal.slug or "journal"
    candidate = f"{prefix}-{base_slug}"
    suffix = 2
    while ArticlePage.objects.filter(slug=candidate).exists():
        candidate = f"{prefix}-{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _unique_static_slug(static_article):
    base_slug = static_article.slug or "article"
    if not ArticlePage.objects.filter(static_slug=base_slug).exists():
        return base_slug
    prefix = static_article.journal.slug or "journal"
    candidate = f"{prefix}-{base_slug}"
    suffix = 2
    while ArticlePage.objects.filter(static_slug=candidate).exists():
        candidate = f"{prefix}-{base_slug}-{suffix}"
        suffix += 1
    return candidate


def get_approved_articles(at=None, *, include_active_release=False):
    """Return reviewed articles that have a currently effective placement."""
    placements = _available_placements(
        at, include_active_release=include_active_release
    ).filter(article_id=OuterRef("pk"))
    return _approved_article_queryset().filter(Exists(placements))


def get_article_context(slug, at=None, *, include_active_release=False):
    article = get_approved_articles(
        at=at, include_active_release=include_active_release
    ).get(static_slug=slug)
    related_journals = list(article.related_journals.all())
    article_title = localized_article_title(article)
    article_abstract = localized_article_abstract(article)
    article_authors = localized_article_authors(article)
    article_keywords = localized_article_keywords(article)
    article_ai_coauthors = localized_article_ai_coauthors(article)
    contributors = tuple(article.contributors.all())
    keywords = _split_csv(article_keywords)
    ai_co_authors = _split_csv(article_ai_coauthors)
    from ai_author_forum.journals.frontend import get_public_editorial_team

    editorial_team = get_public_editorial_team(article.primary_journal, at=at)

    from .category_services import get_live_article_categories

    live_categories = get_live_article_categories(article_id=article.pk)
    primary_category = live_categories.primary
    context = {
        "article": article,
        "article_public_id": str(article.public_id),
        "page": article,
        "static_url": article.get_absolute_url(),
        "primary_journal": article.primary_journal,
        "primary_category": primary_category,
        "primary_category_ancestors": (
            primary_category.get_ancestors() if primary_category else []
        ),
        "related_categories": live_categories.related,
        "related_journals": related_journals,
        "journals": [article.primary_journal, *related_journals],
        "authors": _split_csv(article_authors),
        "authors_text": article_authors,
        "contributors": contributors,
        "keywords": keywords,
        "keywords_text": article_keywords,
        "article_display_title": article_title,
        "article_display_abstract": article_abstract,
        "article_display_body": localized_article_body(article),
        "article_type": article.article_type,
        "article_type_label": article_type_label(article.article_type),
        "review_status": article.review_status,
        "review_status_label": article.get_review_status_display(),
        "author_declaration": article.responsibility_statement,
        "editorial_team": (
            editorial_team
            if article.primary_journal.show_editorial_team_on_article_pages
            else {
                "heading": editorial_team["heading"],
                "groups": [],
                "has_members": False,
            }
        ),
        "ai": {
            "co_authors": ai_co_authors,
            "co_authors_text": article_ai_coauthors,
            "contribution_statement": article.ai_contribution_statement,
            "responsibility_statement": article.responsibility_statement,
            "has_contribution": bool(
                ai_co_authors or article.ai_contribution_statement
            ),
        },
    }
    context.update(get_article_fallback_context(article))
    return context


def get_articles_by_journal(journal_slug, at=None, *, include_active_release=False):
    if not journal_slug:
        return _approved_article_queryset().none()

    placements = (
        _available_placements(at, include_active_release=include_active_release)
        .for_target("journal", journal_slug)
        .filter(article_id=OuterRef("pk"))
    )
    return (
        _approved_article_queryset()
        .filter(Exists(placements))
        .filter(primary_journal__slug=journal_slug)
        .distinct()
    )


def _available_placements(at=None, *, include_active_release=False):
    from ai_author_forum.placements.models import ArticlePlacement

    if include_active_release:
        return ArticlePlacement.objects.available_for_static_release(at=at)
    return ArticlePlacement.objects.available(at=at)


def _approved_article_queryset():
    return (
        ArticlePage.objects.filter(review_status__in=APPROVED_ARTICLE_STATUSES)
        .select_related(
            "primary_journal",
            "latest_revision",
            "approved_version",
            "rejected_version",
        )
        .prefetch_related("related_journals", "contributors")
        .annotate(first_revision_created_at=_first_revision_created_at_subquery())
        .annotate(
            article_created_at=Coalesce(
                "first_revision_created_at",
                "first_published_at",
                "latest_revision_created_at",
                output_field=DateTimeField(),
            )
        )
        .order_by("-article_created_at", "-pk")
    )


def _first_revision_created_at_subquery():
    page_content_type = ContentType.objects.get_for_model(
        Page,
        for_concrete_model=False,
    )
    return Subquery(
        Revision.objects.filter(
            base_content_type=page_content_type,
            object_id=Cast(OuterRef("pk"), output_field=CharField()),
        )
        .order_by("created_at", "id")
        .values("created_at")[:1],
        output_field=DateTimeField(),
    )


def _split_csv(value):
    if not value:
        return []

    return [item.strip() for item in value.split(",") if item.strip()]


# Dynamic category service contract (articles.services.categories).
import sys as _sys  # noqa: E402

from . import category_services as categories  # noqa: E402

_sys.modules[__name__ + ".categories"] = categories
