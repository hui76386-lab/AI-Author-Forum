"""Minimal, privacy-preserving reader share event recording."""

from __future__ import annotations

import hmac
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .capabilities import get_effective_capabilities
from .crypto import security_fingerprint
from .models import (
    IdempotencyRecord,
    InteractionOutbox,
    ReaderActionEvent,
    ReaderIdentity,
)


class ShareEventError(RuntimeError):
    code = "service_degraded"
    status = 503


class ShareNotAllowed(ShareEventError):
    code = "share_disabled"
    status = 403


class ShareIdempotencyConflict(ShareEventError):
    code = "idempotency_conflict"
    status = 409


_EVENT_TYPES = {
    "system_share": ReaderActionEvent.EventType.SHARE_OPENED,
    "copy_link": ReaderActionEvent.EventType.LINK_COPIED,
}
_OUTCOMES = {"completed", "cancelled", "failed"}


def _validated_payload(payload):
    if set(payload) != {"action", "outcome"}:
        raise ValidationError("Share events accept only action and outcome.")
    action = str(payload.get("action") or "")
    outcome = str(payload.get("outcome") or "")
    if action not in _EVENT_TYPES or outcome not in _OUTCOMES:
        raise ValidationError("Share action or outcome is invalid.")
    return action, outcome


def record_share_event(
    *, article_public_id, reader, payload, idempotency_key, request_hash
):
    if not settings.READER_SHARE_UI_ENABLED:
        raise ShareNotAllowed("Reader sharing is disabled.")
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValidationError("Idempotency-Key is required.")
    article_public_id = UUID(str(article_public_id))
    action, outcome = _validated_payload(payload)
    key_hash = security_fingerprint("share-event", idempotency_key)
    event_type = _EVENT_TYPES[action]
    now = timezone.now()

    with transaction.atomic(using="interactions"):
        locked_reader = ReaderIdentity.objects.select_for_update().get(pk=reader.pk)
        if locked_reader.status != ReaderIdentity.Status.ACTIVE:
            raise ShareNotAllowed("Reader is not active.")
        capabilities = get_effective_capabilities(
            article_public_id,
            session_context=object(),
        )
        if not capabilities.can_share:
            raise ShareNotAllowed("Sharing is unavailable for this article.")

        scope = f"share.event:{article_public_id}"
        previous = IdempotencyRecord.objects.filter(
            reader=locked_reader,
            scope=scope,
            key_hash=key_hash,
        ).first()
        if previous:
            if not hmac.compare_digest(previous.request_hash, request_hash):
                raise ShareIdempotencyConflict("Idempotency key payload mismatch.")
            return previous.response_body

        recent = (
            ReaderActionEvent.objects.filter(
                article_public_id=article_public_id,
                reader_public_id=locked_reader.public_id,
                event_type=event_type,
                created_at__gte=now - timedelta(minutes=1),
            )
            .order_by("-created_at")
            .first()
        )
        if recent:
            response_body = {
                "recorded": False,
                "coalesced": True,
                "event_id": str(recent.event_id),
            }
        else:
            event = ReaderActionEvent.objects.create(
                event_type=event_type,
                article_public_id=article_public_id,
                reader_public_id=locked_reader.public_id,
                outcome=outcome,
            )
            InteractionOutbox.objects.create(
                event_type="reader.share.recorded",
                aggregate_type="reader_action_event",
                aggregate_id=str(event.event_id),
                aggregate_version=1,
                payload={
                    "event_id": str(event.event_id),
                    "article_public_id": str(article_public_id),
                    "release_version": capabilities.active_release,
                    "action": action,
                    "outcome": outcome,
                },
            )
            response_body = {
                "recorded": True,
                "coalesced": False,
                "event_id": str(event.event_id),
            }

        IdempotencyRecord.objects.create(
            reader=locked_reader,
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            response_status=202,
            response_body=response_body,
            expires_at=now + timedelta(days=1),
        )
        return response_body
