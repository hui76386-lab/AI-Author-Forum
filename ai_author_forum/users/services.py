from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

SUPER_ADMIN_GROUP_NAME = "超级管理员"
JOURNAL_EDITOR_ACCESS_GROUP_NAME = "子期刊编辑基础访问"


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
        raise PermissionDenied("只有超级管理员可以管理账号。")


def revoke_user_sessions(user) -> int:
    deleted = 0
    for session in Session.objects.all().iterator():
        try:
            session_user_id = session.get_decoded().get("_auth_user_id")
        except Exception:
            continue
        if str(session_user_id) == str(user.pk):
            session.delete()
            deleted += 1
    return deleted


def _lock_active_super_admins():
    User = get_user_model()
    return User.objects.select_for_update().filter(
        account_status=User.AccountStatus.ACTIVE,
        is_active=True,
        groups__name=SUPER_ADMIN_GROUP_NAME,
    )


def _protect_super_admin_change(*, actor, user, confirming_password="") -> None:
    if not user.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists():
        return
    others = _lock_active_super_admins().exclude(pk=user.pk)
    if not others.exists():
        raise ValidationError("不能暂停、停用或撤销最后一个有效超级管理员。")
    if actor.pk == user.pk and not actor.check_password(confirming_password):
        raise ValidationError("操作者变更本人账号时必须再次输入本人密码。")


@transaction.atomic
def create_account(
    *,
    actor,
    username: str,
    email: str,
    display_name: str,
    temporary_password: str,
    institution: str = "",
    job_title: str = "",
    is_super_admin_account: bool = False,
    is_author_account: bool = False,
    assignments: Iterable[dict] = (),
    confirming_password: str = "",
):
    _require_super_admin(actor)
    User = get_user_model()
    email = email.strip().lower()
    if not email:
        raise ValidationError({"email": "邮箱为必填项。"})
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": "该邮箱已被使用。"})
    if not display_name.strip():
        raise ValidationError({"display_name": "姓名为必填项。"})
    validate_password(temporary_password, user=User(username=username, email=email))
    assignment_rows = list(assignments)
    if is_super_admin_account and assignment_rows:
        raise ValidationError("超级管理员不能同时通过账号表单分配子期刊角色。")
    if is_super_admin_account and not actor.check_password(confirming_password):
        raise ValidationError("创建超级管理员账号必须再次输入本人密码。")
    if not is_super_admin_account and not assignment_rows and not is_author_account:
        raise ValidationError("账号必须分配作者、编辑或超级管理员角色。")

    user = User(
        username=username.strip(),
        email=email,
        display_name=display_name.strip(),
        institution=institution.strip(),
        job_title=job_title.strip(),
        account_status=User.AccountStatus.ACTIVE,
        is_active=True,
        is_staff=bool(is_super_admin_account or assignment_rows),
        is_author=bool(is_author_account),
        must_change_password=True,
        created_by=actor,
    )
    user.set_password(temporary_password)
    user.full_clean()
    user.save()

    if is_super_admin_account:
        group, _ = Group.objects.get_or_create(name=SUPER_ADMIN_GROUP_NAME)
        user.groups.add(group)
    else:
        from ai_author_forum.journals.editor_services import appoint_journal_editor

        for assignment in assignment_rows:
            appoint_journal_editor(actor=actor, user=user, **assignment)

    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=user,
        message="创建实名后台账号。",
        metadata={
            "account_status": user.account_status,
            "is_super_admin": is_super_admin_account,
            "is_author": is_author_account,
            "assignment_count": len(assignment_rows),
        },
    )
    return user


def _change_status(*, actor, user, status, reason, confirming_password=""):
    _require_super_admin(actor)
    reason = reason.strip()
    if status != user.AccountStatus.ACTIVE and not reason:
        raise ValidationError({"reason": "暂停或停用账号时必须填写原因。"})
    User = get_user_model()
    locked = User.objects.select_for_update().get(pk=user.pk)
    if status != User.AccountStatus.ACTIVE:
        _protect_super_admin_change(
            actor=actor,
            user=locked,
            confirming_password=confirming_password,
        )
    now = timezone.now()
    locked.account_status = status
    locked.is_active = status == User.AccountStatus.ACTIVE
    locked.status_reason = reason if status != User.AccountStatus.ACTIVE else ""
    locked.suspended_at = now if status == User.AccountStatus.SUSPENDED else None
    locked.deactivated_at = now if status == User.AccountStatus.DEACTIVATED else None
    locked.save(
        update_fields=[
            "account_status",
            "is_active",
            "status_reason",
            "suspended_at",
            "deactivated_at",
        ]
    )
    sessions_revoked = revoke_user_sessions(locked)
    if status != User.AccountStatus.ACTIVE:
        from ai_author_forum.journals.editor_services import (
            clear_invalid_initial_assignments,
        )

        clear_invalid_initial_assignments(user=locked)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message=f"账号状态变更为 {status}。",
        metadata={"reason": reason, "sessions_revoked": sessions_revoked},
    )
    return locked


@transaction.atomic
def suspend_account(*, actor, user, reason, confirming_password=""):
    return _change_status(
        actor=actor,
        user=user,
        status=user.AccountStatus.SUSPENDED,
        reason=reason,
        confirming_password=confirming_password,
    )


@transaction.atomic
def deactivate_account(*, actor, user, reason, confirming_password=""):
    return _change_status(
        actor=actor,
        user=user,
        status=user.AccountStatus.DEACTIVATED,
        reason=reason,
        confirming_password=confirming_password,
    )


@transaction.atomic
def activate_account(*, actor, user):
    _require_super_admin(actor)
    if user.account_status == user.AccountStatus.DEACTIVATED:
        raise ValidationError("已停用账号默认不可恢复；需按高风险账号流程重新确认。")
    return _change_status(
        actor=actor,
        user=user,
        status=user.AccountStatus.ACTIVE,
        reason="",
    )


@transaction.atomic
def reset_account_password(
    *, actor, user, temporary_password: str, confirming_password: str = ""
):
    _require_super_admin(actor)
    User = get_user_model()
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.groups.filter(
        name=SUPER_ADMIN_GROUP_NAME
    ).exists() and not actor.check_password(confirming_password):
        raise ValidationError("重置超级管理员密码必须再次输入本人密码。")
    validate_password(temporary_password, user=locked)
    locked.set_password(temporary_password)
    locked.must_change_password = True
    locked.save(update_fields=["password", "must_change_password"])
    sessions_revoked = revoke_user_sessions(locked)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="重置账号密码并要求首次登录修改。",
        metadata={"sessions_revoked": sessions_revoked},
    )
    return locked


@transaction.atomic
def revoke_super_admin(*, actor, user, confirming_password=""):
    _require_super_admin(actor)
    User = get_user_model()
    locked = User.objects.select_for_update().get(pk=user.pk)
    _protect_super_admin_change(
        actor=actor,
        user=locked,
        confirming_password=confirming_password,
    )
    locked.groups.remove(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
    sessions_revoked = revoke_user_sessions(locked)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="撤销超级管理员角色。",
        metadata={"sessions_revoked": sessions_revoked},
    )
    return locked


@transaction.atomic
def grant_super_admin(*, actor, user):
    _require_super_admin(actor)
    User = get_user_model()
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.account_status != User.AccountStatus.ACTIVE or not locked.is_active:
        raise ValidationError("只能向状态正常的账号授予超级管理员角色。")
    if locked.journal_editor_assignments.filter(is_active=True).exists():
        raise ValidationError("超级管理员不能同时保留有效子期刊编辑任命。")
    group, _ = Group.objects.get_or_create(name=SUPER_ADMIN_GROUP_NAME)
    if locked.groups.filter(pk=group.pk).exists():
        return locked
    locked.groups.add(group)
    locked.is_staff = True
    locked.save(update_fields=["is_staff"])
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="授予超级管理员角色。",
        metadata={},
    )
    return locked


@transaction.atomic
def initialize_super_admin_group(user):
    User = get_user_model()
    if _lock_active_super_admins().exclude(pk=user.pk).exists():
        raise ValidationError("系统已有可用超级管理员，不能运行初始化同步。")
    locked = User.objects.select_for_update().get(pk=user.pk)
    locked.account_status = User.AccountStatus.ACTIVE
    locked.is_active = True
    locked.is_staff = True
    locked.save(update_fields=["account_status", "is_active", "is_staff"])
    group, _ = Group.objects.get_or_create(name=SUPER_ADMIN_GROUP_NAME)
    locked.groups.add(group)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=locked,
        target=locked,
        message="初始化首个超级管理员业务角色。",
        metadata={"technical_recovery": True},
    )
    return locked
