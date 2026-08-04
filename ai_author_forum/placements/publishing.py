from __future__ import annotations

from django.db import transaction

from ai_author_forum.static_publish.automatic import _paths_for_placement_events
from ai_author_forum.static_publish.models import StaticPublishJob


def can_publish_automatically(actor):
    return bool(actor and (actor.is_superuser or (
        actor.has_perm("static_publish.publish_static_site")
        and actor.has_perm("static_publish.publish_category_pages")
    )))


def create_batch_publish(*, batch, events, actor):
    """Create exactly one static-publish job for a successfully committed batch."""
    paths = sorted(_paths_for_placement_events(events))
    if not paths:
        return None, batch.PublishStatus.NOT_STARTED
    automatic = can_publish_automatically(actor)
    job = StaticPublishJob.objects.create(
        scope=StaticPublishJob.Scope.SELECTIVE,
        requested_paths=paths,
        is_automatic=automatic,
        triggered_by=actor,
        summary={
            "trigger": "placement_batch",
            "placement_batch_id": str(batch.pk),
            "placement_batch_number": batch.batch_number,
            "placement_ids": sorted({event.get("placement_id") for event in events if event.get("placement_id") is not None}),
            "requires_publisher_approval": not automatic,
        },
    )
    if automatic:
        from ai_author_forum.static_publish.tasks import run_static_publish
        transaction.on_commit(lambda: run_static_publish.apply_async(args=(job.pk,)), robust=True)
        return job, batch.PublishStatus.QUEUED
    return job, batch.PublishStatus.PENDING_APPROVAL


def sync_batch_publish_status(batch):
    job = batch.publish_job
    if not job:
        return batch.publish_status
    mapping = {
        StaticPublishJob.Status.PENDING: (
            batch.PublishStatus.QUEUED
            if job.is_automatic or job.retry_of_id
            else batch.PublishStatus.PENDING_APPROVAL
        ),
        StaticPublishJob.Status.RUNNING: batch.PublishStatus.PUBLISHING,
        StaticPublishJob.Status.SUCCEEDED: batch.PublishStatus.SUCCEEDED,
        StaticPublishJob.Status.PARTIAL: batch.PublishStatus.FAILED,
        StaticPublishJob.Status.FAILED: batch.PublishStatus.FAILED,
        StaticPublishJob.Status.ROLLED_BACK: batch.PublishStatus.ROLLED_BACK,
    }
    desired = mapping.get(job.status, batch.publish_status)
    if desired != batch.publish_status:
        batch.publish_status = desired
        batch.save(update_fields=("publish_status", "updated_at"))
    return desired
