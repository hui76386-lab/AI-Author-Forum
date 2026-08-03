from __future__ import annotations

from ai_author_forum.site_settings.permissions import (
    get_admin_permission_context,
    is_global_admin,
)

ARTICLE_IMPORT_PERMISSION = "site_settings.import_articles"
ARTICLE_ACCESS_PERMISSION = "site_settings.access_articles"
ARTICLE_EDIT_PERMISSION = "articles.edit_article"
ADMIN_ACCESS_PERMISSION = "wagtailadmin.access_admin"


def can_import_articles(user) -> bool:
    """Return whether the user satisfies the full article-import permission rule."""

    return get_admin_permission_context(user).get("can_import_articles", False)


def can_override_suspicious_article_text(user) -> bool:
    """Only project-wide administrators may force suspicious text through."""

    return is_global_admin(user)


def can_view_article_import_job(user, job) -> bool:
    """Importers see their own tasks; project administrators can see every task."""

    if not can_import_articles(user):
        return False
    return is_global_admin(user) or job.operator_id == getattr(user, "pk", None)
