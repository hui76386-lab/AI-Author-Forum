"""Control-plane outbox delivery for reader capabilities."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from ai_author_forum.reader_interactions.capabilities import (
    CapabilityDenyStore,
    apply_capability_projection as apply_projection,
)

from .models import ControlPlaneOutbox

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    name="ai_author_forum.reader_access.tasks.render_pdf",
    soft_time_limit=180,
    time_limit=210,
)
def render_pdf(self, artifact_public_id):
    from .pdfs import render_artifact

    try:
        artifact = render_artifact(artifact_public_id)
    except Exception as exc:  # noqa: BLE001 - artifact stores a stable error code
        logger.warning(
            "reader_pdf_render_failed artifact_id=%s error_type=%s",
            artifact_public_id,
            type(exc).__name__,
        )
        raise self.retry(
            exc=RuntimeError("Protected PDF render failed."), countdown=15
        ) from None
    return {
        "status": artifact.status,
        "artifact_public_id": str(artifact.public_id),
        "byte_size": artifact.byte_size,
    }


@shared_task(
    bind=True,
    max_retries=8,
    name="ai_author_forum.reader_access.tasks.apply_capability_projection",
)
def apply_capability_projection(self, event_id):
    failure_type = None
    event = None
    try:
        with transaction.atomic(using="default"):
            event = (
                ControlPlaneOutbox.objects.using("default")
                .select_for_update()
                .filter(event_id=event_id, event_type="reader.capability.desired")
                .first()
            )
            if event is None or event.published_at is not None:
                return {"status": "ignored"}
            payload = dict(event.payload or {})
            result, _projection = apply_projection(payload)
            CapabilityDenyStore().clear_if_matches(
                payload["article_public_id"], payload
            )
            ControlPlaneOutbox.objects.mark_published(event.event_id)
    except Exception as exc:  # noqa: BLE001 - task logs only type
        failure_type = type(exc).__name__
        if event is not None:
            ControlPlaneOutbox.objects.record_attempt(
                event.event_id, error=failure_type
            )
    if failure_type:
        logger.warning(
            "reader_capability_projection_failed event_id=%s error_type=%s",
            event_id,
            failure_type,
        )
        raise self.retry(
            exc=RuntimeError("Capability projection failed."), countdown=5
        ) from None
    return {"status": result}


@shared_task(
    name="ai_author_forum.reader_access.tasks.reconcile_capability_projections"
)
def reconcile_capability_projections(limit=100):
    events = list(
        ControlPlaneOutbox.objects.using("default")
        .filter(event_type="reader.capability.desired", published_at__isnull=True)
        .order_by("created_at", "pk")[: int(limit)]
        .values_list("event_id", flat=True)
    )
    for event_id in events:
        apply_capability_projection.delay(str(event_id))
    return {"queued": len(events)}


@shared_task(
    bind=True,
    max_retries=4,
    name="ai_author_forum.reader_access.tasks.apply_moderation_command",
)
def apply_moderation_command(self, command_id):
    from .moderation import apply_moderation_command as apply_command

    try:
        result = apply_command(command_id)
    except Exception as exc:  # noqa: BLE001 - command service records unknown state
        logger.warning(
            "reader_moderation_task_failed command_id=%s error_type=%s",
            command_id,
            type(exc).__name__,
        )
        raise self.retry(
            exc=RuntimeError("Moderation command could not be applied."), countdown=5
        ) from None
    return result.body


@shared_task(name="ai_author_forum.reader_access.tasks.reconcile_moderation_commands")
def reconcile_moderation_commands(limit=100):
    from .moderation import reconcile_moderation_commands as reconcile

    return reconcile(limit=limit)
