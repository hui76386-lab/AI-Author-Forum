from __future__ import annotations

from ai_author_forum.journals.models import ArticleImportScope, JournalEditorAssignment
from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    is_super_admin,
)

ARTICLE_IMPORT_PERMISSION = "site_settings.import_articles"
ARTICLE_ACCESS_PERMISSION = "site_settings.access_articles"
ARTICLE_EDIT_PERMISSION = "articles.edit_article"
ADMIN_ACCESS_PERMISSION = "wagtailadmin.access_admin"


def can_import_articles(user) -> bool:
    """Return whether the user can import in at least one permitted scope."""

    if is_super_admin(user):
        return True
    return any(
        assignment.role
        in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
        or JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
        in (assignment.responsibilities or [])
        for assignment in JournalEditorAssignment.objects.effective().filter(user=user)
    )


def can_import_article_scope(user, *, scope, journal=None) -> bool:
    if is_super_admin(user):
        return True
    if scope != ArticleImportScope.JOURNAL or journal is None:
        return False
    return can_manage_journal(
        user,
        journal,
        JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
    )


def can_override_suspicious_article_text(user) -> bool:
    """Only project-wide administrators may force suspicious text through."""

    return is_super_admin(user)


def can_view_article_import_job(user, job) -> bool:
    """Importers see their own tasks; project administrators can see every task."""

    if is_super_admin(user):
        return True
    return bool(
        job.operator_id == getattr(user, "pk", None)
        and can_import_article_scope(
            user,
            scope=job.import_scope,
            journal=job.target_journal,
        )
    )
