"""Celery tasks for reader verification delivery."""

from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import quote

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .crypto import EmailProtector
from .email import send_magic_link_email
from .models import (
    DownloadGrant,
    EmailVerificationChallenge,
    IdempotencyRecord,
    InteractionOutbox,
    ReaderDeviceFlow,
    ReaderSession,
)
from .services import _audit_device_flow

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=5,
    name="ai_author_forum.reader_interactions.tasks.refresh_comment_snapshot",
)
def refresh_comment_snapshot(self, article_public_id):
    try:
        from .snapshots import rebuild_comment_snapshot

        snapshot = rebuild_comment_snapshot(article_public_id)
    except Exception as exc:  # noqa: BLE001 - retry type only
        logger.warning(
            "reader_comment_snapshot_failed article_id=%s error_type=%s",
            article_public_id,
            type(exc).__name__,
        )
        raise self.retry(
            exc=RuntimeError("Comment snapshot refresh failed."), countdown=10
        ) from None
    return {"status": "ready", "version": snapshot.version}


@shared_task(
    bind=True,
    max_retries=5,
    name="ai_author_forum.reader_interactions.tasks.send_magic_link",
)
def send_magic_link(self, event_id):
    failure_type = None
    challenge_id = None
    with transaction.atomic(using="interactions"):
        event = (
            InteractionOutbox.objects.select_for_update()
            .filter(
                event_id=event_id,
                event_type="reader.email.magic_link.requested",
            )
            .first()
        )
        if event is None or event.published_at is not None:
            return {"status": "ignored"}
        challenge = EmailVerificationChallenge.objects.filter(
            public_id=event.payload.get("challenge_id")
        ).first()
        if (
            challenge is None
            or challenge.status != EmailVerificationChallenge.Status.ISSUED
        ):
            InteractionOutbox.objects.mark_published(event.event_id)
            return {"status": "stale"}

        challenge_id = challenge.public_id
        try:
            protector = EmailProtector.from_settings()
            recipient = protector.decrypt_text(challenge.email_ciphertext)
            token = protector.decrypt_text(event.payload["delivery_token_ciphertext"])
            link = (
                f"{settings.READER_PUBLIC_BASE_URL}"
                f"{reverse('reader_verify_email')}?challenge={challenge.public_id}"
                f"#token={quote(token, safe='')}"
            )
            send_magic_link_email(
                recipient=recipient,
                link=link,
                purpose=challenge.purpose,
                expires_minutes=max(1, settings.READER_MAGIC_LINK_TTL_SECONDS // 60),
            )
        except Exception as exc:  # noqa: BLE001 - provider details must stay redacted
            failure_type = type(exc).__name__
            InteractionOutbox.objects.record_attempt(event.event_id, error=failure_type)
        else:
            InteractionOutbox.objects.mark_published(
                event.event_id, published_at=timezone.now()
            )

    if failure_type:
        logger.warning(
            "reader_magic_link_delivery_failed event_id=%s error_type=%s",
            event.event_id,
            failure_type,
        )
        raise self.retry(
            exc=RuntimeError("Reader email delivery failed."),
            countdown=min(300, 2 ** max(1, int(self.request.retries or 0))),
        ) from None
    logger.info(
        "reader_magic_link_delivered event_id=%s challenge_id=%s",
        event.event_id,
        challenge_id,
    )
    return {"status": "sent"}


@shared_task(
    name="ai_author_forum.reader_interactions.tasks.cleanup_reader_security_records"
)
def cleanup_reader_security_records():
    """Delete only expired security records beyond their short retention windows."""

    now = timezone.now()
    expired_issued = EmailVerificationChallenge.objects.filter(
        expires_at__lt=now,
        status=EmailVerificationChallenge.Status.ISSUED,
    ).update(status=EmailVerificationChallenge.Status.EXPIRED)
    expired_flows = 0
    for flow in ReaderDeviceFlow.objects.filter(
        expires_at__lt=now,
        status__in=(
            ReaderDeviceFlow.Status.PENDING,
            ReaderDeviceFlow.Status.APPROVED,
        ),
    ):
        previous_status = flow.status
        updated = ReaderDeviceFlow.objects.filter(
            pk=flow.pk,
            status=previous_status,
        ).update(status=ReaderDeviceFlow.Status.EXPIRED, updated_at=now)
        if updated:
            expired_flows += updated
            flow.status = ReaderDeviceFlow.Status.EXPIRED
            _audit_device_flow(flow, from_status=previous_status, to_status=flow.status)
    challenge_cutoff = now - timedelta(days=1)
    deleted_flows, _ = ReaderDeviceFlow.objects.filter(
        expires_at__lt=challenge_cutoff,
        status__in=(
            ReaderDeviceFlow.Status.EXPIRED,
            ReaderDeviceFlow.Status.CLAIMED,
            ReaderDeviceFlow.Status.CANCELLED,
            ReaderDeviceFlow.Status.SUPERSEDED,
            ReaderDeviceFlow.Status.DENIED,
        ),
    ).delete()
    stale_challenge_flows, _ = ReaderDeviceFlow.objects.filter(
        challenge__expires_at__lt=challenge_cutoff,
    ).delete()
    deleted_flows += stale_challenge_flows
    deleted_challenges, _ = EmailVerificationChallenge.objects.filter(
        expires_at__lt=challenge_cutoff,
        status__in=(
            EmailVerificationChallenge.Status.EXPIRED,
            EmailVerificationChallenge.Status.CONSUMED,
            EmailVerificationChallenge.Status.SUPERSEDED,
            EmailVerificationChallenge.Status.BLOCKED,
        ),
    ).delete()
    security_cutoff = now - timedelta(days=settings.READER_SECURITY_RETENTION_DAYS)
    deleted_sessions, _ = ReaderSession.objects.filter(
        revoked_at__lt=security_cutoff
    ).delete()
    deleted_idempotency, _ = IdempotencyRecord.objects.filter(
        expires_at__lt=now
    ).delete()
    expired_download_grants = DownloadGrant.objects.filter(
        expires_at__lt=now,
        status=DownloadGrant.Status.ISSUED,
    ).update(status=DownloadGrant.Status.EXPIRED)
    return {
        "expired_issued_challenges": expired_issued,
        "expired_device_flows": expired_flows,
        "deleted_challenges": deleted_challenges,
        "deleted_device_flows": deleted_flows,
        "deleted_sessions": deleted_sessions,
        "deleted_idempotency_records": deleted_idempotency,
        "expired_download_grants": expired_download_grants,
    }
