from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from .display import resolve_article_image
from .models import (
    ArticlePage,
    user_has_article_edit_permission,
    user_has_article_placement_permission,
    user_has_article_review_permission,
)


def prepare_article_admin_row(article, *, user, request=None):
    article.admin_image = resolve_article_image(article, request=request)
    category_assignments = list(article.category_assignments.all())
    article.admin_categories = [
        assignment.category for assignment in category_assignments
    ]
    article.admin_has_primary_category = any(
        assignment.is_primary for assignment in category_assignments
    )
    article.admin_last_editor = getattr(article.latest_revision, "user", None)
    article.admin_can_edit = bool(
        user_has_article_edit_permission(user)
        and article.permissions_for_user(user).can_edit()
    )
    article.admin_can_review = user_has_article_review_permission(user)
    article.admin_can_manage_placements = user_has_article_placement_permission(user)
    article.admin_can_submit_review = bool(
        article.admin_can_edit
        and article.review_status
        in {ArticlePage.ReviewStatus.DRAFT, ArticlePage.ReviewStatus.REJECTED}
    )
    article.admin_static_preview_url = get_active_static_preview_url(article)
    article.admin_urls = {
        "edit": _reverse("wagtailadmin_pages:edit", article.pk),
        "review": _reverse("article_admin:review_detail", article.pk),
        "submit_review": _reverse("article_admin:submit_review", article.pk),
        "preview": _reverse("wagtailadmin_pages:preview_on_edit", article.pk),
        "history": _reverse("wagtailadmin_pages:history", article.pk),
        "placements": _reverse("placements:index") + f"?article={article.pk}",
    }
    return article


def get_active_static_preview_url(article):
    try:
        from ai_author_forum.static_publish.models import StaticManifest

        manifest = StaticManifest.objects.filter(is_active=True).first()
    except Exception:
        return ""
    if not manifest:
        return ""

    expected = article.get_static_output_path().lstrip("/")
    metadata = manifest.metadata or {}
    targets = metadata.get("targets") or []
    for target in targets:
        path = str(target.get("output_path") or target.get("path") or "").lstrip("/")
        if path == expected and target.get("status", "generated") == "generated":
            return article.get_absolute_url()
    return ""


def _reverse(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""
