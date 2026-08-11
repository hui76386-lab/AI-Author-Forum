"""Coalesce placement mutations into safe, selective static publishes."""

from __future__ import annotations

from datetime import timedelta
from math import ceil

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

from .models import StaticManifest, StaticPublishJob, StaticPublishTarget
from .services import get_journal_publish_paths, mark_publish_job_queue_failure

PLACEMENT_CHANGE_COALESCE_KEY = "placement-change"
PLACEMENT_CHANGE_TRIGGER = "placement_change"


def _snapshot(placement):
    """Return only the placement data required after the caller commits."""
    if isinstance(placement, dict):
        return dict(placement)
    return {
        "placement_id": placement.pk,
        "article_id": placement.article_id,
        "article_static_slug": (
            placement.article.static_slug if placement.article_id else ""
        ),
        "target_type": placement.target_type,
        "target_slug": placement.target_slug,
        "target_category_id": placement.target_category_id,
        "is_active": placement.is_active,
    }


def _active_manifest_journal_ids():
    manifest = StaticManifest.objects.filter(is_active=True).first()
    if manifest is None:
        return None
    return {
        int(journal_id)
        for target in (manifest.metadata or {}).get("targets", ())
        if target.get("target_type") == "journal_page"
        for journal_id in (target.get("dependencies") or {}).get("journal_ids", ())
    }


def placement_publish_plan(events):
    """Use a complete journal baseline until the journal exists in the manifest."""
    from ai_author_forum.journals.models import Journal

    snapshots = [_snapshot(event) for event in events if event]
    paths = sorted(_paths_for_placement_events(snapshots))
    plan = {
        "scope": StaticPublishJob.Scope.SELECTIVE,
        "paths": paths,
        "summary": {},
    }
    active_journal_ids = _active_manifest_journal_ids()
    if active_journal_ids is None:
        return plan

    journal_slugs = {
        (event.get("target_slug") or "").strip("/")
        for event in snapshots
        if event.get("target_type") == "journal" and event.get("target_slug")
    }
    if len(journal_slugs) != 1:
        return plan
    journal = Journal.objects.filter(slug=next(iter(journal_slugs))).first()
    if journal is None or journal.pk in active_journal_ids:
        return plan

    return {
        "scope": StaticPublishJob.Scope.JOURNAL,
        "paths": sorted(get_journal_publish_paths(journal)),
        "summary": {
            "publish_mode": "journal_baseline",
            "journal_id": journal.pk,
            "journal_slug": journal.slug,
            "journal_name": journal.name_cn or journal.name,
        },
    }


def queue_placement_publish(*placements, actor=None, reason=PLACEMENT_CHANGE_TRIGGER):
    """Schedule one on-commit automatic selective publish for placement changes.

    ``placements`` may contain live model instances and/or their pre-change
    snapshots.  Including both makes a target move or deactivation refresh the
    old page as well as the new page.
    """
    if not getattr(settings, "STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE", False):
        return
    events = [_snapshot(placement) for placement in placements if placement]
    if not events:
        return
    actor_id = getattr(actor, "pk", None)
    transaction.on_commit(
        lambda: _queue_placement_publish(events, actor_id=actor_id, reason=reason),
        robust=True,
    )


def create_pending_placement_publish(
    *placements, actor=None, reason="placement_change_pending_approval"
):
    """Create a publisher-owned pending selective job without executing it."""
    events = [_snapshot(placement) for placement in placements if placement]
    plan = placement_publish_plan(events)
    paths = plan["paths"]
    if not paths:
        return None
    return StaticPublishJob.objects.create(
        scope=plan["scope"],
        requested_paths=paths,
        is_automatic=False,
        triggered_by=actor,
        summary={
            "trigger": reason,
            "requires_publisher_approval": True,
            "change_count": len(events),
            "placement_ids": sorted(
                {
                    event["placement_id"]
                    for event in events
                    if event.get("placement_id") is not None
                }
            ),
            "publish_events": events,
            "authorized_paths": paths,
            **plan["summary"],
        },
    )


def _queue_placement_publish(events, *, actor_id, reason):
    actor = (
        get_user_model().objects.filter(pk=actor_id).first()
        if actor_id is not None
        else None
    )
    plan = placement_publish_plan(events)
    paths = plan["paths"]
    if not paths:
        return None

    debounce_seconds = max(
        0, int(getattr(settings, "STATIC_PUBLISH_AUTO_DEBOUNCE_SECONDS", 60))
    )
    scheduled_at = timezone.now() + timedelta(seconds=debounce_seconds)
    placement_ids = sorted(
        {
            event["placement_id"]
            for event in events
            if event.get("placement_id") is not None
        }
    )

    coalesce_key = (
        f"{PLACEMENT_CHANGE_COALESCE_KEY}:{actor_id}"
        if actor_id is not None
        else PLACEMENT_CHANGE_COALESCE_KEY
    )
    # A partial unique index guarantees that concurrent web workers for the same
    # actor still share one pending batch without mixing journal-editor scopes.
    # Retry once if another worker wins the create race.
    for attempt in range(2):
        try:
            with transaction.atomic():
                job = (
                    StaticPublishJob.objects.select_for_update()
                    .filter(
                        is_automatic=True,
                        status=StaticPublishJob.Status.PENDING,
                        coalesce_key=coalesce_key,
                    )
                    .first()
                )
                created = job is None
                if created:
                    job = StaticPublishJob.objects.create(
                        scope=plan["scope"],
                        requested_paths=paths,
                        is_automatic=True,
                        coalesce_key=coalesce_key,
                        scheduled_at=scheduled_at,
                        triggered_by_id=actor_id,
                        summary={
                            "trigger": PLACEMENT_CHANGE_TRIGGER,
                            "reason": reason,
                            "change_count": len(events),
                            "placement_ids": placement_ids,
                            "publish_events": events,
                            "authorized_paths": paths,
                            **plan["summary"],
                        },
                    )
                else:
                    summary = dict(job.summary or {})
                    summary["trigger"] = PLACEMENT_CHANGE_TRIGGER
                    summary["reason"] = reason
                    summary["change_count"] = int(summary.get("change_count", 0)) + len(
                        events
                    )
                    summary["placement_ids"] = sorted(
                        set(summary.get("placement_ids", [])) | set(placement_ids)
                    )
                    known_events = {
                        (
                            event.get("placement_id"),
                            event.get("article_id"),
                            event.get("target_type"),
                            event.get("target_slug"),
                            event.get("target_category_id"),
                            event.get("is_active"),
                        ): event
                        for event in summary.get("publish_events", [])
                    }
                    for event in events:
                        key = (
                            event.get("placement_id"),
                            event.get("article_id"),
                            event.get("target_type"),
                            event.get("target_slug"),
                            event.get("target_category_id"),
                            event.get("is_active"),
                        )
                        known_events[key] = event
                    merged_events = list(known_events.values())
                    merged_plan = placement_publish_plan(merged_events)
                    summary["publish_events"] = merged_events
                    for key in (
                        "publish_mode",
                        "journal_id",
                        "journal_slug",
                        "journal_name",
                    ):
                        summary.pop(key, None)
                    summary.update(merged_plan["summary"])
                    job.scope = merged_plan["scope"]
                    job.requested_paths = merged_plan["paths"]
                    summary["authorized_paths"] = job.requested_paths
                    job.scheduled_at = scheduled_at
                    job.summary = summary
                    job.save(
                        update_fields=(
                            "scope",
                            "requested_paths",
                            "scheduled_at",
                            "summary",
                        )
                    )

                record_audit_event(
                    action=AuditAction.PUBLISH,
                    status=AuditStatus.STARTED,
                    actor=actor,
                    target=job,
                    message=(
                        "Automatic static publish queued"
                        if created
                        else "Automatic static publish merged"
                    ),
                    metadata={
                        "trigger": PLACEMENT_CHANGE_TRIGGER,
                        "reason": reason,
                        "placement_ids": placement_ids,
                        "path_count": len(job.requested_paths),
                        "scheduled_at": job.scheduled_at.isoformat(),
                    },
                )
            break
        except IntegrityError:
            if attempt:
                raise
    else:  # pragma: no cover - loop always breaks or raises
        return None

    try:
        from .tasks import run_coalesced_static_publish

        run_coalesced_static_publish.apply_async(args=(job.pk,), eta=job.scheduled_at)
    except Exception as exc:
        mark_publish_job_queue_failure(job, exc)
        raise
    return job


def _paths_for_placement_events(events):
    paths = set()
    category_ids = set()
    for event in events:
        # The static search index contains every currently effective placement,
        # so any placement mutation can add or remove an article from search.
        paths.add("/search/")
        article_slug = (event.get("article_static_slug") or "").strip("/")
        if article_slug:
            paths.add(f"/articles/{article_slug}/")
        target_type = event.get("target_type")
        target_slug = (event.get("target_slug") or "").strip("/")
        if target_type == "main_site":
            paths.add("/")
        elif target_type == "section" and target_slug:
            paths.add(f"/explore-content/{target_slug}/")
        elif target_type == "journal" and target_slug:
            paths.add(f"/journals/{target_slug}/")
        elif target_type == "article" and target_slug:
            paths.add(f"/articles/{target_slug}/")
        elif target_type == "search":
            paths.add("/search/")
        elif target_type == "category" and event.get("target_category_id"):
            category_ids.add(int(event["target_category_id"]))

    if category_ids:
        paths.update(_category_paths(category_ids))
    return paths


def _category_paths(category_ids):
    """Include every current and formerly active category page that can change.

    Current provider targets cover page-count growth; targets from the active
    manifest cover stale trailing pagination that must be deleted after a list
    becomes shorter.
    """
    provider_class = import_string(settings.STATIC_PUBLISH_TARGET_PROVIDER)
    paths = {
        target.url
        for target in provider_class().get_targets()
        if getattr(target, "target_type", "") == "category_page"
        and category_ids.intersection(
            set((getattr(target, "dependencies", {}) or {}).get("category_ids", []))
        )
    }
    active_manifest = StaticManifest.objects.filter(is_active=True).first()
    if active_manifest:
        for target in StaticPublishTarget.objects.filter(
            job_id=active_manifest.job_id, target_type="category_page"
        ).only("path", "dependencies"):
            dependency_ids = set((target.dependencies or {}).get("category_ids", []))
            if category_ids.intersection(dependency_ids):
                paths.add(f"/{target.path.lstrip('/')}")
    return paths


def seconds_until(job, *, now=None):
    now = now or timezone.now()
    if not job.scheduled_at:
        return 0
    return max(0, ceil((job.scheduled_at - now).total_seconds()))
