"""Journal-scoped moderation commands spanning the control and interaction DBs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ai_author_forum.journals.models import Journal
from ai_author_forum.reader_interactions.crypto import security_fingerprint
from ai_author_forum.reader_interactions.models import (
    ArticleCapabilityProjection,
    Comment,
    CommentModerationEvent,
    CommentReport,
    InteractionOutbox,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from .models import ModerationCommand
from .permissions import can_manage_policy

logger = logging.getLogger(__name__)


class ModerationError(RuntimeError):
    code = "moderation_failed"


class ModerationStale(ModerationError):
    code = "stale_version"


class ModerationUnknown(ModerationError):
    code = "unknown"


ALLOWED_ACTIONS = frozenset({"approve", "reject", "hide", "restore", "spam"})
_TRANSITIONS = {
    "approve": {Comment.State.PENDING: Comment.State.PUBLISHED},
    "reject": {Comment.State.PENDING: Comment.State.REJECTED},
    "hide": {
        Comment.State.PENDING: Comment.State.HIDDEN,
        Comment.State.PUBLISHED: Comment.State.HIDDEN,
    },
    "restore": {Comment.State.HIDDEN: Comment.State.PUBLISHED},
    "spam": {
        Comment.State.PENDING: Comment.State.SPAM,
        Comment.State.PUBLISHED: Comment.State.SPAM,
        Comment.State.HIDDEN: Comment.State.SPAM,
    },
}


@dataclass(frozen=True)
class ModerationResult:
    command_id: UUID
    status: str
    body: dict


def _request_hash(*, comment_public_id, action, expected_version, reason, note):
    payload = "|".join(
        (
            str(comment_public_id),
            str(action),
            str(int(expected_version)),
            str(reason or "")[:64],
            str(note or "")[:2000],
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _journal_for_comment(comment):
    journal = Journal.objects.using("default").filter(pk=comment.journal_id).first()
    if journal is None:
        raise ModerationError("Comment journal is unavailable.")
    return journal


def _require_scope(actor, journal, reason):
    if not can_manage_policy(actor, journal):
        raise PermissionDenied("无权审核该期刊评论。")
    if not str(reason or "").strip():
        from ai_author_forum.site_settings.access_control import is_super_admin

        if is_super_admin(actor):
            raise PermissionDenied("超级管理员审核必须填写原因。")


def _audit(*, status, command, message, error_code=""):
    result = dict(command.result_body or {})
    AuditLog.record(
        action=AuditAction.MODERATION,
        status=status,
        actor=command.actor,
        target_type="Comment",
        target_id=str(command.comment_public_id),
        target_label="reader-comment",
        message=message,
        metadata={
            "command_id": str(command.command_id),
            "action": command.action,
            "journal_id": command.journal_id,
            "article_public_id": str(command.article_public_id),
            "expected_version": command.expected_version,
            "actual_version": result.get("version"),
            "from_state": result.get("from_state"),
            "to_state": result.get("state"),
            "event_id": result.get("event_id"),
            "release_version": result.get("release_version"),
            "reason": command.reason,
            "result": status,
            "status": command.status,
            **({"error_code": error_code} if error_code else {}),
        },
        request_id=str(command.request_id),
    )


def _command_payload(command):
    return {
        "command_id": str(command.command_id),
        "comment_id": str(command.comment_public_id),
        "action": command.action,
        "status": command.status,
        "expected_version": command.expected_version,
        "result": dict(command.result_body or {}),
        "error_code": command.error_code or None,
    }


def create_moderation_command(
    *,
    actor,
    comment_public_id,
    action,
    expected_version,
    reason="",
    note="",
    idempotency_key="",
    request_id=None,
    enqueue=True,
):
    """Create a journal-scoped command and emit a non-PII started audit record."""

    comment_public_id = UUID(str(comment_public_id))
    action = str(action or "").strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValidationError("Unsupported moderation action.")
    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Expected comment version is invalid.") from exc
    reason = str(reason or "").strip()[:64]
    note = str(note or "").strip()[:2000]
    if not reason and action in {"reject", "spam", "hide"}:
        raise ValidationError("A moderation reason is required.")
    comment = (
        Comment.objects.using("interactions")
        .filter(public_id=comment_public_id)
        .first()
    )
    if comment is None:
        raise ValidationError("Comment does not exist.")
    journal = _journal_for_comment(comment)
    _require_scope(actor, journal, reason)
    request_hash = _request_hash(
        comment_public_id=comment_public_id,
        action=action,
        expected_version=expected_version,
        reason=reason,
        note=note,
    )
    key_hash = (
        security_fingerprint("reader-moderation-idempotency", str(idempotency_key))
        if str(idempotency_key or "").strip()
        else None
    )
    with transaction.atomic(using="default"):
        if key_hash:
            existing = (
                ModerationCommand.objects.using("default")
                .select_for_update()
                .filter(idempotency_key_hash=key_hash)
                .first()
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise ValidationError("Idempotency key payload mismatch.")
                return ModerationResult(
                    existing.command_id, existing.status, _command_payload(existing)
                )
        command = ModerationCommand.objects.using("default").create(
            comment_public_id=comment_public_id,
            journal_id=journal.pk,
            article_public_id=comment.article_public_id,
            action=action,
            expected_version=expected_version,
            reason=reason,
            note=note,
            idempotency_key_hash=key_hash,
            request_hash=request_hash,
            actor=actor,
            request_id=request_id or uuid4(),
            started_at=timezone.now(),
        )
        _audit(
            status=AuditStatus.STARTED, command=command, message="Moderation started"
        )
        if enqueue:
            transaction.on_commit(
                lambda: enqueue_moderation_command(command.command_id)
            )
        return ModerationResult(
            command.command_id, command.status, _command_payload(command)
        )


def enqueue_moderation_command(command_id):
    from .tasks import apply_moderation_command

    apply_moderation_command.apply_async(
        args=[str(command_id)], queue="reader_comments", argsrepr="(<redacted>,)"
    )


def _apply_interaction_command(command):
    with transaction.atomic(using="interactions"):
        comment = (
            Comment.objects.using("interactions")
            .select_for_update()
            .filter(public_id=command.comment_public_id)
            .first()
        )
        if comment is None:
            raise ModerationError("Comment does not exist.")
        if comment.version != command.expected_version:
            raise ModerationStale("Comment version changed.")
        transition = _TRANSITIONS[command.action].get(comment.state)
        if transition is None:
            raise ModerationError("Comment state cannot perform this action.")
        now = timezone.now()
        previous_state = comment.state
        comment.state = transition
        comment.version += 1
        if transition == Comment.State.PUBLISHED:
            comment.published_at = comment.published_at or now
        comment.save(
            using="interactions",
            update_fields=("state", "version", "published_at", "updated_at"),
        )
        report_status = None
        if command.action in {"hide", "reject", "spam"}:
            report_status = CommentReport.Status.RESOLVED
        elif command.action in {"approve", "restore"}:
            report_status = CommentReport.Status.DISMISSED
        if report_status:
            CommentReport.objects.using("interactions").filter(
                comment=comment,
                status=CommentReport.Status.OPEN,
            ).update(status=report_status, resolved_at=now)
        projection = (
            ArticleCapabilityProjection.objects.using("interactions")
            .filter(article_public_id=comment.article_public_id)
            .values("active_release")
            .first()
        )
        release_version = (projection or {}).get("active_release", "")
        event = CommentModerationEvent.objects.using("interactions").create(
            comment=comment,
            from_state=previous_state,
            to_state=transition,
            action=command.action,
            actor_type=CommentModerationEvent.ActorType.EDITOR,
            actor_id=str(command.actor_id),
            reason=command.reason,
            note=command.note,
            command_id=command.command_id,
            request_id=command.request_id,
        )
        InteractionOutbox.objects.using("interactions").create(
            event_type="reader.comment.moderated",
            aggregate_type="comment",
            aggregate_id=str(comment.public_id),
            aggregate_version=comment.version,
            payload={
                "comment_id": str(comment.public_id),
                "article_public_id": str(comment.article_public_id),
                "state": transition,
                "command_id": str(command.command_id),
                "release_version": release_version,
            },
        )
        return event, {
            "comment_id": str(comment.public_id),
            "from_state": previous_state,
            "state": comment.state,
            "version": comment.version,
            "release_version": release_version,
        }


def _finish_command(command_id, *, status, result=None, error_code=""):
    now = timezone.now()
    updates = {
        "status": status,
        "result_body": result or {},
        "error_code": error_code,
        "completed_at": now,
        "updated_at": now,
    }
    ModerationCommand.objects.using("default").filter(pk=command_id).update(**updates)
    return ModerationCommand.objects.using("default").get(pk=command_id)


def apply_moderation_command(command_id):
    command = (
        ModerationCommand.objects.using("default")
        .select_related("actor")
        .filter(command_id=command_id)
        .first()
    )
    if command is None:
        raise ValidationError("Moderation command does not exist.")
    if command.status == ModerationCommand.Status.APPLIED:
        return ModerationResult(
            command.command_id, command.status, _command_payload(command)
        )
    try:
        event, result = _apply_interaction_command(command)
    except ModerationStale as exc:
        command = _finish_command(
            command.pk,
            status=ModerationCommand.Status.FAILED,
            error_code=exc.code,
        )
        _audit(
            status=AuditStatus.FAILURE,
            command=command,
            message="Moderation rejected due to stale version",
            error_code=exc.code,
        )
        return ModerationResult(
            command.command_id, command.status, _command_payload(command)
        )
    except Exception as exc:  # noqa: BLE001 - unknown is deliberately fail-closed
        error_code = type(exc).__name__.lower()[:64]
        command = _finish_command(
            command.pk,
            status=ModerationCommand.Status.UNKNOWN,
            error_code=error_code,
        )
        _audit(
            status=AuditStatus.FAILURE,
            command=command,
            message="Moderation result is unknown",
            error_code=error_code,
        )
        logger.warning(
            "reader_moderation_unknown command_id=%s error_type=%s",
            command.command_id,
            type(exc).__name__,
        )
        return ModerationResult(
            command.command_id, command.status, _command_payload(command)
        )
    command = _finish_command(
        command.pk,
        status=ModerationCommand.Status.APPLIED,
        result={**result, "event_id": str(event.event_id)},
    )
    _audit(status=AuditStatus.SUCCESS, command=command, message="Moderation applied")
    try:
        from ai_author_forum.reader_interactions.comments import _public_change

        _public_change(command.article_public_id)
    except Exception as exc:  # noqa: BLE001 - content commit already succeeded
        logger.warning(
            "reader_moderation_snapshot_enqueue_failed command_id=%s error_type=%s",
            command.command_id,
            type(exc).__name__,
        )
    return ModerationResult(
        command.command_id, command.status, _command_payload(command)
    )


def batch_moderate_comments(*, actor, items):
    """Create independent commands; one bad item cannot roll back the batch."""

    results = []
    for item in list(items or []):
        try:
            result = create_moderation_command(actor=actor, **dict(item))
        except (PermissionDenied, ValidationError, ModerationError) as exc:
            results.append(
                {
                    "comment_id": str(item.get("comment_public_id", "")),
                    "status": "failed",
                    "error_code": getattr(exc, "code", "invalid_request"),
                }
            )
        else:
            results.append(result.body)
    return results


def reconcile_moderation_commands(limit=100):
    """Reconcile commands after a cross-database timeout without guessing success."""

    commands = list(
        ModerationCommand.objects.using("default")
        .filter(
            status__in=(
                ModerationCommand.Status.PENDING,
                ModerationCommand.Status.UNKNOWN,
            )
        )
        .order_by("created_at", "pk")[: int(limit)]
    )
    reconciled = 0
    for command in commands:
        event = (
            CommentModerationEvent.objects.using("interactions")
            .filter(command_id=command.command_id)
            .order_by("created_at")
            .first()
        )
        if event is None:
            continue
        command = _finish_command(
            command.pk,
            status=ModerationCommand.Status.APPLIED,
            result={"event_id": str(event.event_id), "state": event.to_state},
        )
        _audit(
            status=AuditStatus.SUCCESS, command=command, message="Moderation reconciled"
        )
        reconciled += 1
    return {"checked": len(commands), "reconciled": reconciled}
