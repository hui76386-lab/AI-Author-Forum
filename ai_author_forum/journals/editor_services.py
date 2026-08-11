from __future__ import annotations

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from ai_author_forum.site_settings.access_control import can_manage_journal_field
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.users.services import (
    JOURNAL_EDITOR_ACCESS_GROUP_NAME,
    SUPER_ADMIN_GROUP_NAME,
    create_account,
    ensure_journal_editor_access_group,
    revoke_user_sessions,
)

from .models import Journal, JournalEditorAssignment, JournalStatus

JOURNAL_EDITOR_ACCOUNT_ROLE_PRESETS = {
    role: tuple(JournalEditorAssignment.ALL_RESPONSIBILITIES)
    for role in JournalEditorAssignment.Role.values
}


def _is_super_admin(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.account_status == user.AccountStatus.ACTIVE
        and user.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists()
    )


def _require_super_admin(actor) -> None:
    if not _is_super_admin(actor):
        raise PermissionDenied("只有超级管理员可以分配或结束子期刊角色。")


def _validate_editor_user(user) -> None:
    if not user.is_active or user.account_status != user.AccountStatus.ACTIVE:
        raise ValidationError("只能任命状态正常的实名后台账号。")
    if user.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists():
        raise ValidationError("超级管理员不能同时持有子期刊编辑任命。")


def _close_expired_assignments(*, actor, journal, role=None) -> None:
    now = timezone.now()
    expired = JournalEditorAssignment.objects.select_for_update().filter(
        journal=journal,
        is_active=True,
        ends_at__lte=now,
    )
    if role is not None:
        expired = expired.filter(role=role)
    for assignment in expired.select_related("user"):
        assignment.is_active = False
        assignment.ended_at = assignment.ended_at or now
        assignment.ended_by = assignment.ended_by or actor
        assignment.end_reason = assignment.end_reason or "任期到期自动结束。"
        assignment.save()
        sessions_revoked = revoke_user_sessions(assignment.user)
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=assignment,
            message="到期子期刊编辑任命已结束。",
            metadata={
                "expired_at": assignment.ends_at.isoformat(),
                "sessions_revoked": sessions_revoked,
            },
        )
        sync_editor_access_group(assignment.user)


def _normalize_public_profile(*, user, role, public_profile):
    profile = public_profile or {}
    return {
        "public_name": (profile.get("public_name") or user.display_name).strip(),
        "public_affiliation": (
            profile.get("public_affiliation") or user.institution
        ).strip(),
        "public_role_label": (
            profile.get("public_role_label")
            or JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
        ).strip(),
        "display_order": int(profile.get("display_order") or 0),
        "show_publicly": bool(profile.get("show_publicly", True)),
    }


def sync_editor_access_group(user):
    has_effective_assignment = (
        JournalEditorAssignment.objects.effective().filter(user=user).exists()
    )
    if has_effective_assignment:
        group = ensure_journal_editor_access_group()
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.groups.add(group)
    else:
        group = Group.objects.filter(name=JOURNAL_EDITOR_ACCESS_GROUP_NAME).first()
        if group is not None:
            user.groups.remove(group)
        revoke_user_sessions(user)
    return has_effective_assignment


def journal_editor_account_responsibilities(role):
    try:
        return list(JOURNAL_EDITOR_ACCOUNT_ROLE_PRESETS[role])
    except KeyError as exc:
        raise ValidationError({"role": "不支持的子期刊角色。"}) from exc


@transaction.atomic
def create_journal_editor_account(
    *,
    actor,
    journal,
    role,
    username,
    temporary_password,
    email,
    display_name,
    institution="",
):
    """Create one account and its assignment for the fixed current journal."""
    _require_super_admin(actor)
    locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
    responsibilities = journal_editor_account_responsibilities(role)
    return create_account(
        actor=actor,
        username=username,
        email=email,
        display_name=display_name,
        institution=institution,
        temporary_password=temporary_password,
        assignments=(
            {
                "journal": locked_journal,
                "role": role,
                "responsibilities": responsibilities,
                "public_profile": {
                    "public_name": display_name,
                    "public_affiliation": institution,
                    "public_role_label": (
                        JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
                    ),
                    "display_order": 0,
                    "show_publicly": True,
                },
            },
        ),
    )


@transaction.atomic
def appoint_journal_editor(
    *,
    actor,
    user,
    journal,
    role,
    responsibilities,
    public_profile,
):
    _require_super_admin(actor)
    locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
    _validate_editor_user(user)
    if role not in JournalEditorAssignment.Role.values:
        raise ValidationError({"role": "不支持的子期刊角色。"})
    _close_expired_assignments(actor=actor, journal=locked_journal, role=role)
    if (
        role
        in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
        and JournalEditorAssignment.objects.filter(
            journal=locked_journal,
            role=role,
            is_active=True,
        ).exists()
    ):
        raise ValidationError("该子期刊已有在任人员，请使用原子交接服务。")
    assignment = JournalEditorAssignment(
        user=user,
        journal=locked_journal,
        role=role,
        responsibilities=list(responsibilities or []),
        created_by=actor,
        **_normalize_public_profile(
            user=user,
            role=role,
            public_profile=public_profile,
        ),
    )
    assignment.save()
    sync_editor_access_group(user)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=assignment,
        message="创建子期刊编辑任命。",
        metadata={
            "journal_id": locked_journal.pk,
            "user_id": user.pk,
            "role": role,
            "responsibilities": assignment.responsibilities,
        },
    )
    return assignment


def _replace_editor(*, actor, journal, new_user, role, reason):
    _require_super_admin(actor)
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "角色交接必须填写原因。"})
    locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
    _close_expired_assignments(actor=actor, journal=locked_journal, role=role)
    old_assignment = (
        JournalEditorAssignment.objects.select_for_update()
        .filter(journal=locked_journal, role=role, is_active=True)
        .first()
    )
    if old_assignment and old_assignment.user_id == new_user.pk:
        return old_assignment
    _validate_editor_user(new_user)
    now = timezone.now()
    if old_assignment:
        old_assignment.is_active = False
        old_assignment.ends_at = now
        old_assignment.ended_at = now
        old_assignment.ended_by = actor
        old_assignment.end_reason = reason
        old_assignment.save()
        sessions_revoked = revoke_user_sessions(old_assignment.user)
    new_assignment = JournalEditorAssignment(
        user=new_user,
        journal=locked_journal,
        role=role,
        responsibilities=list(JournalEditorAssignment.ALL_RESPONSIBILITIES),
        created_by=actor,
        starts_at=now,
        **_normalize_public_profile(
            user=new_user,
            role=role,
            public_profile=None,
        ),
    )
    new_assignment.save()
    if old_assignment:
        old_assignment.replaced_by_assignment = new_assignment
        old_assignment.save(update_fields=["replaced_by_assignment", "updated_at"])
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=old_assignment,
            message="结束旧子期刊编辑任命。",
            metadata={
                "reason": reason,
                "replacement_id": new_assignment.pk,
                "sessions_revoked": sessions_revoked,
            },
        )
        sync_editor_access_group(old_assignment.user)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=new_assignment,
        message="完成子期刊编辑角色交接。",
        metadata={
            "reason": reason,
            "previous_assignment_id": getattr(old_assignment, "pk", None),
        },
    )
    sync_editor_access_group(new_user)
    return new_assignment


@transaction.atomic
def replace_chief_editor(*, actor, journal, new_user, reason):
    return _replace_editor(
        actor=actor,
        journal=journal,
        new_user=new_user,
        role=JournalEditorAssignment.Role.CHIEF_EDITOR,
        reason=reason,
    )


@transaction.atomic
def replace_executive_editor(*, actor, journal, new_user, reason):
    return _replace_editor(
        actor=actor,
        journal=journal,
        new_user=new_user,
        role=JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        reason=reason,
    )


@transaction.atomic
def end_journal_editor_assignment(*, actor, assignment, reason):
    _require_super_admin(actor)
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "结束任命必须填写原因。"})
    locked = (
        JournalEditorAssignment.objects.select_for_update()
        .select_related("journal", "user")
        .get(pk=assignment.pk)
    )
    Journal.objects.select_for_update().get(pk=locked.journal_id)
    if not locked.is_active:
        return locked
    if (
        locked.role == JournalEditorAssignment.Role.CHIEF_EDITOR
        and locked.journal.status == JournalStatus.ACTIVE
    ):
        raise ValidationError("启用中的子期刊不能直接结束最后一名主编辑。")
    now = timezone.now()
    locked.is_active = False
    locked.ends_at = now
    locked.ended_at = now
    locked.ended_by = actor
    locked.end_reason = reason
    locked.save()
    sessions_revoked = revoke_user_sessions(locked.user)
    clear_invalid_initial_assignments(user=locked.user, journal=locked.journal)
    sync_editor_access_group(locked.user)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="结束子期刊编辑任命。",
        metadata={"reason": reason, "sessions_revoked": sessions_revoked},
    )
    return locked


@transaction.atomic
def update_editor_assignment_profile(
    *,
    actor,
    assignment,
    responsibilities=None,
    public_profile=None,
):
    locked = (
        JournalEditorAssignment.objects.select_for_update()
        .select_related("journal", "user")
        .get(pk=assignment.pk)
    )
    actor_assignment = (
        JournalEditorAssignment.objects.effective()
        .filter(
            user=actor,
            journal=locked.journal,
        )
        .first()
    )
    can_manage = _is_super_admin(actor) or (
        actor_assignment
        and actor_assignment.role
        in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
    )
    can_edit_self = actor.pk == locked.user_id
    if not can_manage and not can_edit_self:
        raise PermissionDenied("无权维护该编辑公开资料。")
    if responsibilities is not None:
        if (
            not can_manage
            or locked.role != JournalEditorAssignment.Role.ASSOCIATE_EDITOR
        ):
            raise PermissionDenied("无权调整该任命的日常职责。")
        locked.responsibilities = list(responsibilities)
    profile = public_profile or {}
    for field in (
        "public_name",
        "public_affiliation",
        "public_role_label",
        "display_order",
        "show_publicly",
    ):
        if field in profile:
            if field in {"display_order", "show_publicly"} and not can_manage:
                raise PermissionDenied(
                    "只有本刊主编辑或常务副编辑可调整排序和公开状态。"
                )
            setattr(locked, field, profile[field])
    locked.save()
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="更新子期刊编辑职责或公开资料。",
        metadata={"responsibilities": locked.responsibilities},
    )
    return locked


@transaction.atomic
def update_editorial_team_settings(*, actor, journal, values):
    locked = Journal.objects.select_for_update().get(pk=journal.pk)
    assignment = (
        JournalEditorAssignment.objects.effective()
        .filter(
            user=actor,
            journal=locked,
            role__in=(
                JournalEditorAssignment.Role.CHIEF_EDITOR,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
            ),
        )
        .first()
    )
    if not _is_super_admin(actor) and assignment is None:
        raise PermissionDenied("只有超级管理员或本刊主编辑团队可以调整展示设置。")
    before = {
        "editorial_team_heading": locked.editorial_team_heading,
        "show_editorial_team_on_article_pages": (
            locked.show_editorial_team_on_article_pages
        ),
    }
    locked.editorial_team_heading = values["editorial_team_heading"].strip()
    locked.show_editorial_team_on_article_pages = bool(
        values.get("show_editorial_team_on_article_pages")
    )
    locked.full_clean()
    locked.save(
        update_fields=(
            "editorial_team_heading",
            "show_editorial_team_on_article_pages",
            "updated_at",
        )
    )
    AuditLog.record(
        action=AuditAction.CONFIGURE,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="更新子期刊编辑团队展示设置。",
        metadata={
            "before": before,
            "after": {
                "editorial_team_heading": locked.editorial_team_heading,
                "show_editorial_team_on_article_pages": (
                    locked.show_editorial_team_on_article_pages
                ),
            },
        },
    )
    return locked


@transaction.atomic
def update_journal_profile(*, actor, journal, values):
    locked = Journal.objects.select_for_update().get(pk=journal.pk)
    changed = {}
    before = {}
    for field_name, value in values.items():
        if not can_manage_journal_field(actor, locked, field_name):
            raise PermissionDenied(f"无权修改期刊字段：{field_name}")
        old_value = getattr(locked, field_name)
        old_pk = getattr(old_value, "pk", old_value)
        new_pk = getattr(value, "pk", value)
        if old_pk != new_pk:
            before[field_name] = old_pk
            changed[field_name] = new_pk
            setattr(locked, field_name, value)
    if not changed:
        return locked
    if changed.get("status") == JournalStatus.ACTIVE:
        now = timezone.now()
        chief_count = (
            JournalEditorAssignment.objects.filter(
                journal=locked,
                role=JournalEditorAssignment.Role.CHIEF_EDITOR,
                is_active=True,
                user__is_active=True,
                user__account_status="active",
            )
            .filter(
                models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
                models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now),
            )
            .count()
        )
        if chief_count != 1:
            raise ValidationError(
                {
                    "status": (
                        "启用前必须准备且仅保留一名有效主编辑；"
                        f"当前找到 {chief_count} 名。"
                    )
                }
            )
    locked.full_clean()
    locked.save(update_fields=(*changed.keys(), "updated_at"))
    AuditLog.record(
        action=AuditAction.CONFIGURE,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="更新子期刊资料。",
        metadata={"before": before, "after": changed},
    )
    return locked


def clear_invalid_initial_assignments(*, user=None, journal=None):
    from ai_author_forum.articles.models import ArticlePage

    queryset = ArticlePage.objects.filter(
        review_status=ArticlePage.ReviewStatus.SUBMITTED,
        assigned_initial_editor__isnull=False,
    )
    if user is not None:
        queryset = queryset.filter(assigned_initial_editor=user)
    if journal is not None:
        queryset = queryset.filter(primary_journal=journal)
    valid_user_ids = (
        JournalEditorAssignment.objects.effective()
        .filter(journal_id=models.OuterRef("primary_journal_id"))
        .values("user_id")
    )
    return queryset.exclude(assigned_initial_editor_id__in=valid_user_ids).update(
        assigned_initial_editor=None,
        assigned_by=None,
        assigned_at=None,
        assignment_request_id=None,
    )
