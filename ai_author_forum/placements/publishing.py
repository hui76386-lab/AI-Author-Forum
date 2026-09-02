from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from ai_author_forum.articles.review_services import has_valid_final_approval
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalEditorAssignment,
)
from ai_author_forum.site_settings.access_control import (
    can_maintain_placement_target,
    can_manage_placement_target,
    get_journal_editor_assignment,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.automatic import (
    _paths_for_placement_events,
    _snapshot,
    placement_publish_plan,
)
from ai_author_forum.static_publish.models import StaticPublishJob

from .models import ArticlePlacement, PlacementBatch


def _event_target(event):
    target_type = event.get("target_type")
    if target_type == ArticlePlacement.TargetType.JOURNAL:
        return Journal.objects.filter(slug=event.get("target_slug", "")).first()
    if target_type == ArticlePlacement.TargetType.CATEGORY:
        return JournalCategory.objects.filter(
            pk=event.get("target_category_id")
        ).first()
    if target_type == ArticlePlacement.TargetType.ARTICLE:
        from ai_author_forum.articles.models import ArticlePage

        return ArticlePage.objects.filter(
            static_slug=event.get("target_slug", "")
        ).first()
    return None


def _publish_events(events):
    return [_snapshot(event) for event in events if event]


def can_publish_automatically(actor, events):
    """Allow only platform admins or journal-scoped editor publication jobs."""
    if is_super_admin(actor):
        return True
    snapshots = _publish_events(events)
    if not snapshots:
        return False
    placement_ids = {
        event.get("placement_id")
        for event in snapshots
        if event.get("placement_id") is not None
    }
    placements = {
        placement.pk: placement
        for placement in ArticlePlacement.objects.filter(
            pk__in=placement_ids
        ).select_related("article", "article__primary_journal")
    }
    if len(placements) != len(placement_ids):
        return False
    for event in snapshots:
        placement = placements.get(event.get("placement_id"))
        if (
            placement is None
            or placement.source != ArticlePlacement.Source.MANUAL
            or placement.article_id != event.get("article_id")
            or placement.article.static_slug != event.get("article_static_slug")
        ):
            return False
        assignment = get_journal_editor_assignment(
            actor, placement.article.primary_journal
        )
        if assignment is None:
            return False
        target = _event_target(event)
        if assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR:
            if not can_manage_placement_target(
                actor, placement.article, event.get("target_type"), target
            ):
                return False
        elif not (
            placement.article.last_static_published_at
            and can_maintain_placement_target(
                actor, placement.article, event.get("target_type"), target
            )
        ):
            return False
    return True


def require_automatic_placement_publish_job(job):
    """Recheck a journal editor's exact automatic job inside the worker."""
    summary = dict(job.summary or {})
    if (
        job.scope not in {
            StaticPublishJob.Scope.SELECTIVE,
            StaticPublishJob.Scope.JOURNAL,
        }
        or not job.is_automatic
        or summary.get("requires_publisher_approval")
        or summary.get("trigger") not in {"placement_batch", "placement_change"}
    ):
        raise PermissionDenied("This is not an authorized journal publish job.")
    events = list(summary.get("publish_events") or [])
    if not events:
        placements = list(
            ArticlePlacement.objects.filter(
                pk__in=summary.get("placement_ids") or []
            ).select_related("article", "article__primary_journal")
        )
        events = _publish_events(placements)
    if not can_publish_automatically(job.triggered_by, events):
        raise PermissionDenied("Journal publish scope or editor assignment is invalid.")
    event_placement_ids = sorted(
        {
            event.get("placement_id")
            for event in events
            if event.get("placement_id") is not None
        }
    )
    if sorted(summary.get("placement_ids") or []) != event_placement_ids:
        raise PermissionDenied("Journal publish placement ids do not match its events.")
    plan = placement_publish_plan(events)
    calculated_paths = plan["paths"]
    authorized_paths = sorted(summary.get("authorized_paths") or calculated_paths)
    if (
        job.scope != plan["scope"]
        or authorized_paths != calculated_paths
        or sorted(job.requested_paths or []) != authorized_paths
    ):
        raise PermissionDenied("Journal publish paths do not match placement changes.")
    return True


def supersede_pending_chief_publish_jobs(job_ids):
    """Merge old approval-waiting chief jobs into one scoped automatic job."""
    ids = sorted({int(job_id) for job_id in job_ids})
    if not ids:
        raise PermissionDenied("No pending journal publish jobs were selected.")
    with transaction.atomic():
        pending_jobs = list(
            StaticPublishJob.objects.select_for_update(of=("self",))
            .select_related("triggered_by")
            .filter(pk__in=ids)
            .order_by("pk")
        )
        if len(pending_jobs) != len(ids):
            raise PermissionDenied("One or more pending publish jobs do not exist.")
        actor_ids = {job.triggered_by_id for job in pending_jobs}
        if len(actor_ids) != 1 or None in actor_ids:
            raise PermissionDenied("Pending jobs must belong to one chief editor.")
        for job in pending_jobs:
            if job.status != StaticPublishJob.Status.PENDING or not (
                job.summary or {}
            ).get("requires_publisher_approval"):
                raise PermissionDenied("Only approval-waiting jobs may be superseded.")
        placement_ids = sorted(
            {
                placement_id
                for job in pending_jobs
                for placement_id in (job.summary or {}).get("placement_ids", [])
            }
        )
        placements = list(
            ArticlePlacement.objects.filter(pk__in=placement_ids)
            .select_related(
                "article", "article__approved_version", "article__primary_journal"
            )
            .order_by("pk")
        )
        if len(placements) != len(placement_ids):
            raise PermissionDenied("A pending job references a missing placement.")
        for placement in placements:
            article = placement.article
            if not article.approved_version_id or not has_valid_final_approval(
                article, article.approved_version
            ):
                raise PermissionDenied(
                    "A pending article no longer has current final approval."
                )
        actor = pending_jobs[0].triggered_by
        events = _publish_events(placements)
        if not can_publish_automatically(actor, events):
            raise PermissionDenied(
                "The chief editor no longer owns this journal scope."
            )
        plan = placement_publish_plan(events)
        paths = plan["paths"]
        replacement = StaticPublishJob.objects.create(
            scope=plan["scope"],
            requested_paths=paths,
            is_automatic=True,
            coalesce_key=f"chief-transition:{actor.pk}:{ids[0]}-{ids[-1]}",
            triggered_by=actor,
            summary={
                "trigger": "placement_batch",
                "policy_transition": "chief_editor_direct_publish",
                "supersedes_pending_jobs": ids,
                "placement_ids": placement_ids,
                "publish_events": events,
                "authorized_paths": paths,
                "requires_publisher_approval": False,
                **plan["summary"],
            },
        )
        PlacementBatch.objects.filter(publish_job_id__in=ids).update(
            publish_job=replacement,
            publish_status=PlacementBatch.PublishStatus.QUEUED,
            updated_at=timezone.now(),
        )
        finished_at = timezone.now()
        for old_job in pending_jobs:
            old_job.status = StaticPublishJob.Status.FAILED
            old_job.finished_at = finished_at
            old_job.error = (
                f"Superseded without execution by automatic scoped job "
                f"#{replacement.pk}."
            )
            old_job.save(update_fields=("status", "finished_at", "error"))
            AuditLog.record(
                action=AuditAction.PUBLISH,
                status=AuditStatus.SUCCESS,
                actor=actor,
                target=old_job,
                message="Pending chief-editor publish superseded by scoped automation.",
                metadata={"replacement_job_id": replacement.pk},
            )
        AuditLog.record(
            action=AuditAction.PUBLISH,
            status=AuditStatus.STARTED,
            actor=actor,
            target=replacement,
            message="Chief-editor scoped automatic publish queued.",
            metadata={
                "superseded_job_ids": ids,
                "placement_ids": placement_ids,
                "paths": paths,
            },
        )
        from ai_author_forum.static_publish.tasks import run_static_publish

        transaction.on_commit(
            lambda: run_static_publish.apply_async(args=(replacement.pk,)), robust=True
        )
    return replacement


def create_batch_publish(*, batch, events, actor):
    """Create exactly one static-publish job for a successfully committed batch."""
    publish_events = _publish_events(events)
    plan = placement_publish_plan(publish_events)
    paths = plan["paths"]
    if not paths:
        return None, batch.PublishStatus.NOT_STARTED
    automatic = can_publish_automatically(actor, publish_events)
    coalesce_key = f"placement-batch:{batch.pk}" if automatic else ""
    job = StaticPublishJob.objects.create(
        scope=plan["scope"],
        requested_paths=paths,
        is_automatic=automatic,
        coalesce_key=coalesce_key,
        triggered_by=actor,
        summary={
            "trigger": "placement_batch",
            "placement_batch_id": str(batch.pk),
            "placement_batch_number": batch.batch_number,
            "placement_ids": sorted(
                {
                    event.get("placement_id")
                    for event in events
                    if event.get("placement_id") is not None
                }
            ),
            "publish_events": publish_events,
            "authorized_paths": paths,
            "requires_publisher_approval": not automatic,
            **plan["summary"],
        },
    )
    if automatic:
        from ai_author_forum.static_publish.tasks import run_static_publish

        transaction.on_commit(
            lambda: run_static_publish.apply_async(args=(job.pk,)), robust=True
        )
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
