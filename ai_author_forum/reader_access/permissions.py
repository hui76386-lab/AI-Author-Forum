"""Object-level policy permissions for the three journal editor roles."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied

from ai_author_forum.journals.models import JournalEditorAssignment
from ai_author_forum.site_settings.access_control import is_super_admin

EDITOR_ROLES = frozenset(
    {
        JournalEditorAssignment.Role.CHIEF_EDITOR,
        JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
    }
)


def can_manage_policy(user, journal) -> bool:
    if is_super_admin(user):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return (
        JournalEditorAssignment.objects.effective()
        .filter(
            user=user,
            journal=journal,
            role__in=EDITOR_ROLES,
        )
        .exists()
    )


def require_policy_permission(user, journal, *, reason=""):
    if not can_manage_policy(user, journal):
        raise PermissionDenied("无权管理该期刊的读者互动政策。")
    if is_super_admin(user) and not str(reason or "").strip():
        raise PermissionDenied("超级管理员修改互动政策必须填写原因。")
    return True


def accessible_journals(user, queryset=None):
    from ai_author_forum.journals.models import Journal

    queryset = queryset if queryset is not None else Journal.objects.all()
    if is_super_admin(user):
        return queryset
    journal_ids = (
        JournalEditorAssignment.objects.effective()
        .filter(
            user=user,
            role__in=EDITOR_ROLES,
        )
        .values("journal_id")
    )
    return queryset.filter(pk__in=journal_ids)
