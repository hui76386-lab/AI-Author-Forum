from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from ai_author_forum.site_settings.models import AuditAction

from .models import StaticPublishJob
from .services import StaticPublisher


@shared_task(bind=True, autoretry_for=(), track_started=True)
def run_static_publish(self, job_id):
    job = StaticPublishJob.objects.get(pk=job_id)
    StaticPublisher().build(job)
    return {"job_id": job.pk, "status": job.status, "version": job.version}


@shared_task(bind=True, autoretry_for=(), track_started=True)
def run_coalesced_static_publish(self, job_id):
    """Build an automatic placement batch only after its merge window closes."""
    from .automatic import seconds_until

    with transaction.atomic():
        job = StaticPublishJob.objects.select_for_update().get(pk=job_id)
        if not job.is_automatic or job.status != StaticPublishJob.Status.PENDING:
            return {"job_id": job.pk, "status": job.status, "skipped": True}
        wait_seconds = seconds_until(job, now=timezone.now())
        if wait_seconds:
            run_coalesced_static_publish.apply_async(
                args=(job.pk,), eta=job.scheduled_at
            )
            return {
                "job_id": job.pk,
                "status": job.status,
                "deferred_seconds": wait_seconds,
            }
        # Claim before invoking the publisher so duplicate ETA messages cannot
        # start the same versioned release twice.
        job.status = StaticPublishJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=("status", "started_at"))

    publisher = StaticPublisher()
    try:
        publisher.build(job)
    except Exception as exc:
        # ``StaticPublisher`` records render/build failures itself.  A lock
        # acquisition failure happens before that internal handling, however,
        # and would otherwise leave this worker-claimed job stuck in running.
        job.refresh_from_db()
        if job.status == StaticPublishJob.Status.RUNNING:
            publisher.mark_worker_preflight_failure(
                job,
                action=AuditAction.PUBLISH,
                error=exc,
                metadata={"stage": "automatic_worker"},
            )
        raise
    job.refresh_from_db()
    return {"job_id": job.pk, "status": job.status, "version": job.version}


def _load_task_actor(user_id):
    if user_id is None:
        return None
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        raise PermissionDenied("发起操作的用户不存在")
    return user


@shared_task(bind=True, autoretry_for=(), track_started=True)
def retry_static_publish(self, job_id, user_id=None):
    job = StaticPublishJob.objects.select_related("retry_of").get(pk=job_id)
    publisher = StaticPublisher()
    try:
        user = _load_task_actor(user_id)
    except PermissionDenied as exc:
        publisher.mark_worker_preflight_failure(
            job,
            action=AuditAction.RETRY,
            error=exc,
            metadata={"requested_user_id": user_id, "retry_of": job.retry_of_id},
        )
        raise
    publisher.run_retry(job, user)
    return {"job_id": job.pk, "status": job.status, "version": job.version}


@shared_task(bind=True, autoretry_for=(), track_started=True)
def rollback_static_publish(self, job_id, user_id=None):
    job = StaticPublishJob.objects.get(pk=job_id)
    publisher = StaticPublisher()
    try:
        user = _load_task_actor(user_id)
    except PermissionDenied as exc:
        publisher.mark_worker_preflight_failure(
            job,
            action=AuditAction.ROLLBACK,
            error=exc,
            metadata={
                "requested_user_id": user_id,
                "version": str(job.rollback_version or job.version),
            },
        )
        raise
    publisher.run_rollback(job, user)
    return {"job_id": job.pk, "status": job.status, "version": job.version}
