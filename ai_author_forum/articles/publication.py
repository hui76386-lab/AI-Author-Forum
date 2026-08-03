from django.db import transaction
from django.utils import timezone

from .models import ArticlePage

ARTICLE_SOURCE_PREFIX = "articles.ArticlePage:"
APPROVED_REVIEW_STATUSES = (
    ArticlePage.ReviewStatus.APPROVED,
    ArticlePage.ReviewStatus.PUBLISHED,
)

LEGACY_REVIEW_STATUS_BY_PUBLICATION = {
    ArticlePage.PublicationStatus.APPROVED: "approved",
    ArticlePage.PublicationStatus.PLACED: "placed",
    ArticlePage.PublicationStatus.BUILT: "built",
    ArticlePage.PublicationStatus.PUBLISHED: "published",
    ArticlePage.PublicationStatus.OFFLINE: "offline",
}


def article_id_from_source(source):
    """Return the canonical ArticlePage id encoded in a publish target source."""
    source = str(source or "")
    if not source.startswith(ARTICLE_SOURCE_PREFIX):
        return None
    try:
        return int(source.removeprefix(ARTICLE_SOURCE_PREFIX))
    except (TypeError, ValueError):
        return None


def article_output_path(article):
    """Return the canonical manifest path used by the static target provider."""
    return f"articles/{article.static_slug}/index.html"


def prepare_articles_for_build(targets):
    """Mark publishable article targets as placed before rendering starts."""
    article_ids = {
        article_id
        for target in targets
        if (article_id := article_id_from_source(getattr(target, "source", "")))
    }
    if not article_ids:
        return set()

    ArticlePage.objects.filter(pk__in=article_ids).exclude(
        publication_status=ArticlePage.PublicationStatus.PUBLISHED
    ).update(publication_status=ArticlePage.PublicationStatus.PLACED)
    sync_legacy_article_states(article_ids)
    return article_ids


def record_article_build_success(source, version, *, at=None):
    """Record a successful article render without hiding an active publication."""
    article_id = article_id_from_source(source)
    if article_id is None:
        return None

    at = at or timezone.now()
    article = ArticlePage.objects.filter(pk=article_id).first()
    if article is None:
        return None

    article.build_version = version
    article.last_built_at = at
    article.publish_failure_reason = ""
    if article.publication_status != ArticlePage.PublicationStatus.PUBLISHED:
        article.publication_status = ArticlePage.PublicationStatus.BUILT
    article.save(
        clean=False,
        bypass_article_permission_check=True,
        update_fields=(
            "publication_status",
            "build_version",
            "publish_failure_reason",
            "last_built_at",
        ),
    )
    sync_legacy_article_states({article.pk})
    return article


def record_article_build_failure(source, error):
    """Keep the per-article failure reason while the active release stays unchanged."""
    article_id = article_id_from_source(source)
    if article_id is None:
        return None

    article = ArticlePage.objects.filter(pk=article_id).first()
    if article is None:
        return None

    article.publish_failure_reason = str(error)
    if not article.publication_status:
        article.publication_status = ArticlePage.PublicationStatus.PLACED
    article.save(
        clean=False,
        bypass_article_permission_check=True,
        update_fields=("publication_status", "publish_failure_reason"),
    )
    sync_legacy_article_states({article.pk})
    return article


def sync_article_placement_status(article_id, *, at=None):
    """Synchronize the pre-build APPROVED/PLACED boundary for one article.

    BUILT and PUBLISHED are not downgraded here: only activation of another
    manifest can move an article from those delivery states.
    """
    article = ArticlePage.objects.filter(pk=article_id).first()
    if article is None:
        return None

    if article.review_status not in APPROVED_REVIEW_STATUSES:
        next_status = (
            ArticlePage.PublicationStatus.OFFLINE if article.publication_status else ""
        )
    elif article.publication_status in (
        ArticlePage.PublicationStatus.BUILT,
        ArticlePage.PublicationStatus.PUBLISHED,
    ):
        next_status = article.publication_status
    else:
        from ai_author_forum.placements.models import ArticlePlacement

        has_placement = (
            ArticlePlacement.objects.available(at=at)
            .filter(article_id=article.pk)
            .exists()
        )
        next_status = (
            ArticlePage.PublicationStatus.PLACED
            if has_placement
            else ArticlePage.PublicationStatus.APPROVED
        )

    if article.publication_status != next_status:
        ArticlePage.objects.filter(pk=article.pk).update(publication_status=next_status)
        article.publication_status = next_status
        sync_legacy_article_states({article.pk})
    return article


def sync_articles_to_active_manifest(manifest, *, at=None):
    """Make canonical article state match the manifest that is now active.

    An article present in the active manifest is PUBLISHED. An article that had
    already entered the build/publication lifecycle but is absent becomes
    OFFLINE. Approved articles without delivery history remain APPROVED or
    PLACED according to their current effective placements.
    """
    at = at or timezone.now()
    manifest_paths = {
        item.get("path") for item in (manifest.files or []) if item.get("path")
    }
    changed = []

    with transaction.atomic():
        articles = list(ArticlePage.objects.select_for_update().all())
        for article in articles:
            included = article_output_path(article) in manifest_paths
            if included:
                article.publication_status = ArticlePage.PublicationStatus.PUBLISHED
                article.published_version = manifest.version
                article.last_static_published_at = at
                article.publish_failure_reason = ""
            elif (
                article.publication_status
                in (
                    ArticlePage.PublicationStatus.BUILT,
                    ArticlePage.PublicationStatus.PUBLISHED,
                    ArticlePage.PublicationStatus.OFFLINE,
                )
                or article.build_version
                or article.published_version
            ):
                article.publication_status = ArticlePage.PublicationStatus.OFFLINE
                article.published_version = ""
            elif article.review_status in APPROVED_REVIEW_STATUSES:
                from ai_author_forum.placements.models import ArticlePlacement

                has_placement = (
                    ArticlePlacement.objects.available(at=at)
                    .filter(article_id=article.pk)
                    .exists()
                )
                article.publication_status = (
                    ArticlePage.PublicationStatus.PLACED
                    if has_placement
                    else ArticlePage.PublicationStatus.APPROVED
                )
            elif article.publication_status:
                article.publication_status = ArticlePage.PublicationStatus.OFFLINE
                article.published_version = ""
            else:
                continue
            changed.append(article)

        if changed:
            ArticlePage.objects.bulk_update(
                changed,
                fields=(
                    "publication_status",
                    "published_version",
                    "publish_failure_reason",
                    "last_static_published_at",
                ),
            )
            sync_legacy_article_states({article.pk for article in changed})
    return changed


def sync_legacy_article_states(article_ids):
    """Mirror canonical delivery state to compatibility StaticArticle rows.

    ArticlePage is authoritative after the one-time import. StaticArticle only
    receives a compatibility status and build version; it never owns the active
    publication state again.
    """
    article_ids = set(article_ids or ())
    if not article_ids:
        return

    articles = ArticlePage.objects.filter(
        pk__in=article_ids,
        source_static_article_id__isnull=False,
    ).only(
        "source_static_article_id",
        "review_status",
        "publication_status",
        "build_version",
        "static_slug",
    )
    review_fallback = {
        ArticlePage.ReviewStatus.DRAFT: "draft",
        ArticlePage.ReviewStatus.SUBMITTED: "review",
        ArticlePage.ReviewStatus.APPROVED: "approved",
        ArticlePage.ReviewStatus.PUBLISHED: "approved",
        ArticlePage.ReviewStatus.REJECTED: "offline",
    }

    from ai_author_forum.journals.models import StaticArticle

    for article in articles:
        legacy_status = LEGACY_REVIEW_STATUS_BY_PUBLICATION.get(
            article.publication_status,
            review_fallback.get(article.review_status, "draft"),
        )
        StaticArticle.objects.filter(pk=article.source_static_article_id).update(
            review_status=legacy_status,
            build_version=article.build_version,
            static_output_path=article.get_static_output_path(),
        )
