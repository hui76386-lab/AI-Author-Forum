from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ai_author_forum.articles.review_services import has_valid_final_approval
from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from .batch_services import snapshot_placement
from .models import ArticlePlacement, LayoutSlot, PlacementBatch, PlacementBatchItem
from .publishing import create_batch_publish
from .selectors import mark_journal_used
from .services import (
    has_placement_permission,
    require_placement_scope,
    validate_placement_schedule,
)

MAINTENANCE_OPERATIONS = {
    PlacementBatch.Operation.DEACTIVATE,
    PlacementBatch.Operation.REACTIVATE,
    PlacementBatch.Operation.UPDATE_SCHEDULE,
    PlacementBatch.Operation.PIN,
    PlacementBatch.Operation.UNPIN,
    PlacementBatch.Operation.MOVE,
    PlacementBatch.Operation.COPY,
    PlacementBatch.Operation.CANCEL_FUTURE,
    PlacementBatch.Operation.REPUBLISH,
}


def create_maintenance_batch(*, actor, operation, placement_ids, options=None):
    if not has_placement_permission(actor, "change"):
        raise PermissionDenied
    if operation not in MAINTENANCE_OPERATIONS:
        raise ValidationError("Choose a supported placement maintenance operation.")
    ids = [int(pk) for pk in placement_ids]
    if not ids or len(ids) != len(set(ids)):
        raise ValidationError("Choose one or more distinct placements.")
    placements = list(
        ArticlePlacement.objects.filter(
            pk__in=ids, source=ArticlePlacement.Source.MANUAL
        )
        .select_related("article")
        .order_by("pk")
    )
    if len(placements) != len(ids):
        raise ValidationError(
            "One or more selected placements are not manually managed placements."
        )
    for placement in placements:
        require_placement_scope(actor, placement)
        if operation == PlacementBatch.Operation.COPY:
            require_placement_scope(actor, placement, action="add")
    batch = PlacementBatch.objects.create(
        mode=PlacementBatch.Mode.BULK_MAINTENANCE,
        operation=operation,
        strict_mode=True,
        current_step="review",
        created_by=actor,
        updated_by=actor,
        options=options or {},
    )
    for order, placement in enumerate(placements, start=1):
        PlacementBatchItem.objects.create(
            batch=batch,
            article=placement.article,
            placement=placement,
            sort_order=order * 10,
        )
    return batch


def _ensure_batch_owner(batch, actor):
    if (
        batch.created_by_id
        and batch.created_by_id != actor.pk
        and not is_super_admin(actor)
    ):
        raise PermissionDenied


def _ensure_same_group(placements):
    groups = {
        (p.target_type, p.target_slug, p.target_category_id, p.slot_id)
        for p in placements
    }
    if len(groups) != 1:
        raise ValidationError(
            "This operation requires placements from one target and one slot."
        )


def _write_failure(batch, actor, exc, ip_address):
    batch.refresh_from_db()
    batch.status = PlacementBatch.Status.FAILED
    batch.failure_count = batch.items.count()
    batch.executed_at = timezone.now()
    batch.updated_by = actor
    batch.save(
        update_fields=(
            "status",
            "failure_count",
            "executed_at",
            "updated_by",
            "updated_at",
        )
    )
    AuditLog.record(
        action=AuditAction.CONFIGURE,
        status=AuditStatus.FAILURE,
        actor=actor,
        target=batch,
        message="Placement maintenance batch failed and was rolled back.",
        metadata={
            "batch_id": str(batch.pk),
            "batch_number": batch.batch_number,
            "operation": batch.operation,
            "error": str(exc),
            "ip_address": ip_address,
        },
        ip_address=ip_address,
    )


def _ensure_articles_placeable(placements):
    for placement in placements:
        article = placement.article
        if article.review_status not in {
            article.ReviewStatus.APPROVED,
            article.ReviewStatus.PUBLISHED,
        }:
            raise ValidationError(f"{article.title}: article is not approved.")
        if not article.approved_version_id or not has_valid_final_approval(
            article, article.approved_version
        ):
            raise ValidationError(
                f"{article.title}: current revision has no chief-editor final approval."
            )
        if (
            not article.primary_journal_id
            or article.primary_journal.status != JournalStatus.ACTIVE
        ):
            raise ValidationError(f"{article.title}: source journal is inactive.")


def _option_datetime(options, key):
    """Read a JSON-safe ISO datetime or a datetime supplied by service callers."""
    value = (options or {}).get(key)
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = parse_datetime(value)
    if not isinstance(value, datetime):
        raise ValidationError(f"{key} must be a valid date and time.")
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _schedule_options(options):
    if not (options or {}).get("schedule_configured"):
        raise ValidationError(
            "Configure a start time, end time, or an open-ended schedule before validation."
        )
    starts_at = _option_datetime(options, "starts_at")
    ends_at = _option_datetime(options, "ends_at")
    if starts_at and ends_at and ends_at <= starts_at:
        raise ValidationError("The end time must be later than the start time.")
    return starts_at, ends_at


def _destination(options):
    target_type = (options or {}).get("target_type")
    target_slug = ((options or {}).get("target_slug") or "").strip("/")
    slot = LayoutSlot.objects.filter(
        pk=(options or {}).get("slot_id"), is_active=True
    ).first()
    journal = Journal.objects.filter(
        slug=target_slug, status=JournalStatus.ACTIVE
    ).first()
    if target_type != ArticlePlacement.TargetType.JOURNAL:
        raise ValidationError("Move and copy require a journal target.")
    if not slot or slot.scope != LayoutSlot.Scope.JOURNAL:
        raise ValidationError("Select an active journal slot.")
    if not journal:
        raise ValidationError("Select an active destination journal.")
    return target_type, target_slug, slot, journal


def _ensure_destination_membership(placements, journal):
    for placement in placements:
        article = placement.article
        if article.primary_journal_id != journal.pk:
            raise ValidationError(
                f"{article.title}: article does not belong to {journal}."
            )


def _ensure_placement_scopes(placements, actor):
    for placement in placements:
        require_placement_scope(actor, placement)


def _check_destination_capacity(
    *, placements, slot, target_type, target_slug, operation
):
    current = ArticlePlacement.objects.filter(
        slot=slot,
        target_type=target_type,
        target_slug=target_slug,
        is_active=True,
        source=ArticlePlacement.Source.MANUAL,
    )
    if operation == PlacementBatch.Operation.MOVE:
        current = current.exclude(pk__in=[placement.pk for placement in placements])
    if current.count() + len(placements) > slot.max_items:
        raise ValidationError(
            f"Slot {slot.code} does not have capacity for all selected placements."
        )


def precheck_maintenance_batch(batch, *, actor):
    """Validate a maintenance draft without mutating formal placements.

    Execution repeats every state-dependent check under database locks; reviewing a
    draft neither reserves capacity nor changes ``ArticlePlacement`` facts.
    """
    if not has_placement_permission(actor, "change"):
        raise PermissionDenied
    batch = PlacementBatch.objects.get(pk=batch.pk)
    _ensure_batch_owner(batch, actor)
    items = list(
        batch.items.select_related(
            "placement",
            "placement__article",
            "placement__article__primary_journal",
            "placement__slot",
        ).order_by("sort_order", "pk")
    )
    placements = [item.placement for item in items if item.placement_id]
    errors = []
    if batch.is_executed:
        errors.append("This maintenance batch has already been executed.")
    if not placements or len(placements) != len(items):
        return [*errors, "Select one or more manually managed placements."]
    _ensure_placement_scopes(placements, actor)
    try:
        options = batch.options or {}
        operation = batch.operation
        if operation not in MAINTENANCE_OPERATIONS:
            raise ValidationError("Unsupported maintenance operation.")
        if operation not in {
            PlacementBatch.Operation.DEACTIVATE,
            PlacementBatch.Operation.CANCEL_FUTURE,
        }:
            _ensure_articles_placeable(placements)
        if operation in {PlacementBatch.Operation.PIN, PlacementBatch.Operation.UNPIN}:
            _ensure_same_group(placements)
        elif operation == PlacementBatch.Operation.DEACTIVATE:
            if not (options.get("reason") or "").strip():
                errors.append("A deactivation reason is required.")
        elif operation == PlacementBatch.Operation.CANCEL_FUTURE:
            if not (options.get("reason") or "").strip():
                errors.append("A cancellation reason is required.")
            now = timezone.now()
            if any(
                not placement.starts_at or placement.starts_at <= now
                for placement in placements
            ):
                errors.append("Only placements that have not started may be cancelled.")
        elif operation == PlacementBatch.Operation.REACTIVATE:
            _ensure_articles_placeable(placements)
            for placement in placements:
                candidate = ArticlePlacement.objects.get(pk=placement.pk)
                candidate.is_active = True
                validate_placement_schedule(candidate)
                candidate.full_clean()
        elif operation == PlacementBatch.Operation.UPDATE_SCHEDULE:
            starts_at, ends_at = _schedule_options(options)
            for placement in placements:
                candidate = ArticlePlacement.objects.get(pk=placement.pk)
                candidate.starts_at = starts_at
                candidate.ends_at = ends_at
                if candidate.is_active:
                    validate_placement_schedule(candidate)
                candidate.full_clean()
        elif operation in {
            PlacementBatch.Operation.MOVE,
            PlacementBatch.Operation.COPY,
        }:
            target_type, target_slug, slot, journal = _destination(options)
            if operation == PlacementBatch.Operation.MOVE:
                _ensure_same_group(placements)
            _ensure_articles_placeable(placements)
            _ensure_destination_membership(placements, journal)
            _check_destination_capacity(
                placements=placements,
                slot=slot,
                target_type=target_type,
                target_slug=target_slug,
                operation=operation,
            )
    except ValidationError as exc:
        errors.extend(exc.messages)
    return errors


def _locked_placements(batch):
    items = list(batch.items.select_for_update().order_by("sort_order", "pk"))
    placement_ids = [item.placement_id for item in items]
    if not items or any(not pk for pk in placement_ids):
        raise ValidationError("No placements were selected.")
    placements = list(
        ArticlePlacement.objects.select_for_update(of=("self",))
        .filter(pk__in=placement_ids, source=ArticlePlacement.Source.MANUAL)
        .select_related("article", "article__primary_journal", "slot")
        .order_by("pk")
    )
    by_id = {placement.pk: placement for placement in placements}
    if len(by_id) != len(placement_ids):
        raise ValidationError(
            "One or more selected placements are no longer manually managed placements."
        )
    return items, [by_id[pk] for pk in placement_ids]


def _save_item_result(item, placement, *, before, execution_status):
    item.placement = placement
    item.before_snapshot = before
    item.after_snapshot = snapshot_placement(placement)
    item.validation_status = PlacementBatchItem.ValidationStatus.PASSED
    item.execution_status = execution_status
    item.error_code = ""
    item.error_message = ""
    item.save()


def execute_maintenance_batch(batch, *, actor, ip_address=None):
    """Strictly execute a maintenance command, or roll every formal change back."""
    if not has_placement_permission(actor, "change"):
        raise PermissionDenied
    try:
        with transaction.atomic():
            batch = PlacementBatch.objects.select_for_update().get(pk=batch.pk)
            _ensure_batch_owner(batch, actor)
            if batch.is_executed:
                raise ValidationError(
                    "This maintenance batch has already been executed."
                )
            items, placements = _locked_placements(batch)
            _ensure_placement_scopes(placements, actor)
            before_by_id = {
                placement.pk: snapshot_placement(placement) for placement in placements
            }
            before = list(before_by_id.values())
            options = batch.options or {}
            events = []
            execution_status = PlacementBatchItem.ExecutionStatus.UPDATED
            operation = batch.operation

            if operation not in {
                PlacementBatch.Operation.DEACTIVATE,
                PlacementBatch.Operation.CANCEL_FUTURE,
            }:
                _ensure_articles_placeable(placements)

            # Re-run the draft checks under locks before any mutation.  This is
            # intentionally not delegated to the unlocked precheck function.
            if operation in {
                PlacementBatch.Operation.PIN,
                PlacementBatch.Operation.UNPIN,
            }:
                _ensure_same_group(placements)
                value = operation == PlacementBatch.Operation.PIN
                for placement in placements:
                    placement.is_pinned = value
                    placement.save(update_fields=("is_pinned", "updated_at"))
                    events.append(placement)
            elif operation == PlacementBatch.Operation.DEACTIVATE:
                reason = (options.get("reason") or "").strip()
                if not reason:
                    raise ValidationError("A deactivation reason is required.")
                now = timezone.now()
                for placement in placements:
                    placement.is_active = False
                    placement.metadata = {
                        **(placement.metadata or {}),
                        "deactivation_reason": reason,
                        "deactivated_at": now.isoformat(),
                    }
                    placement.save(
                        update_fields=("is_active", "metadata", "updated_at")
                    )
                    events.append(placement)
            elif operation == PlacementBatch.Operation.CANCEL_FUTURE:
                reason = (options.get("reason") or "").strip()
                if not reason:
                    raise ValidationError("A cancellation reason is required.")
                now = timezone.now()
                if any(
                    not placement.starts_at or placement.starts_at <= now
                    for placement in placements
                ):
                    raise ValidationError(
                        "Only placements that have not started may be cancelled."
                    )
                for placement in placements:
                    placement.is_active = False
                    placement.metadata = {
                        **(placement.metadata or {}),
                        "cancel_reason": reason,
                        "cancelled_at": now.isoformat(),
                    }
                    placement.save(
                        update_fields=("is_active", "metadata", "updated_at")
                    )
                    events.append(placement)
            elif operation == PlacementBatch.Operation.REACTIVATE:
                _ensure_articles_placeable(placements)
                locked_slots = {
                    slot.pk: slot
                    for slot in LayoutSlot.objects.select_for_update().filter(
                        pk__in=[p.slot_id for p in placements]
                    )
                }
                for placement in placements:
                    placement.slot = locked_slots[placement.slot_id]
                    placement.is_active = True
                    validate_placement_schedule(placement, lock=True)
                    placement.full_clean()
                    placement.save()
                    events.append(placement)
            elif operation == PlacementBatch.Operation.UPDATE_SCHEDULE:
                starts_at, ends_at = _schedule_options(options)
                locked_slots = {
                    slot.pk: slot
                    for slot in LayoutSlot.objects.select_for_update().filter(
                        pk__in=[p.slot_id for p in placements]
                    )
                }
                for placement in placements:
                    placement.slot = locked_slots[placement.slot_id]
                    placement.starts_at = starts_at
                    placement.ends_at = ends_at
                    if placement.is_active:
                        validate_placement_schedule(placement, lock=True)
                    placement.full_clean()
                    placement.save()
                    events.append(placement)
            elif operation in {
                PlacementBatch.Operation.MOVE,
                PlacementBatch.Operation.COPY,
            }:
                target_type, target_slug, slot, journal = _destination(options)
                slot = LayoutSlot.objects.select_for_update().get(pk=slot.pk)
                if operation == PlacementBatch.Operation.MOVE:
                    _ensure_same_group(placements)
                _ensure_articles_placeable(placements)
                _ensure_destination_membership(placements, journal)
                _check_destination_capacity(
                    placements=placements,
                    slot=slot,
                    target_type=target_type,
                    target_slug=target_slug,
                    operation=operation,
                )
                execution_status = (
                    PlacementBatchItem.ExecutionStatus.UPDATED
                    if operation == PlacementBatch.Operation.MOVE
                    else PlacementBatchItem.ExecutionStatus.CREATED
                )
                for order, placement in enumerate(placements, start=1):
                    destination = (
                        placement
                        if operation == PlacementBatch.Operation.MOVE
                        else ArticlePlacement(
                            article=placement.article,
                            source=ArticlePlacement.Source.MANUAL,
                            is_active=placement.is_active,
                            starts_at=placement.starts_at,
                            ends_at=placement.ends_at,
                            is_pinned=placement.is_pinned,
                            override_title=placement.override_title,
                            override_summary=placement.override_summary,
                            override_image=placement.override_image,
                            override_image_alt=placement.override_image_alt,
                        )
                    )
                    destination.slot = slot
                    destination.target_type = target_type
                    destination.target_slug = target_slug
                    destination.target_category = None
                    destination.sort_order = order * 10
                    destination.full_clean()
                    destination.save()
                    events.append(destination)
                mark_journal_used(user=actor, journal=journal)
            elif operation == PlacementBatch.Operation.REPUBLISH:
                events.extend(placements)
            else:
                raise ValidationError("Unsupported maintenance operation.")

            after = [snapshot_placement(placement) for placement in events]
            job, publish_status = create_batch_publish(
                batch=batch, events=[*before, *after], actor=actor
            )
            for item, placement in zip(items, events, strict=True):
                original_id = item.placement_id
                _save_item_result(
                    item,
                    placement,
                    before=before_by_id.get(original_id, {}),
                    execution_status=execution_status,
                )
            batch.status = PlacementBatch.Status.SUCCEEDED
            batch.executed_at = timezone.now()
            batch.updated_by = actor
            batch.success_count = len(events)
            batch.failure_count = 0
            batch.skipped_count = 0
            batch.publish_job = job
            batch.publish_status = publish_status
            batch.save()
            AuditLog.record(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.SUCCESS,
                actor=actor,
                target=batch,
                message="Placement maintenance batch executed.",
                metadata={
                    "batch_id": str(batch.pk),
                    "batch_number": batch.batch_number,
                    "operation": operation,
                    "count": len(events),
                    "before": before,
                    "after": after,
                    "publish_job_id": job.pk if job else None,
                    "publish_status": publish_status,
                    "ip_address": ip_address,
                },
                ip_address=ip_address,
            )
            return batch
    except Exception as exc:
        _write_failure(batch, actor, exc, ip_address)
        raise


def export_placements_csv(queryset):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "article_title",
            "article_slug",
            "journal",
            "target",
            "slot",
            "starts_at",
            "ends_at",
            "active",
            "batch_number",
            "publish_status",
            "operator",
            "operation_time",
        ]
    )
    placement_ids = list(queryset.values_list("pk", flat=True))
    item_rows = (
        PlacementBatchItem.objects.filter(placement_id__in=placement_ids)
        .select_related("batch", "batch__created_by")
        .order_by("placement_id", "-batch__executed_at", "-batch__updated_at")
    )
    batches = {}
    for item in item_rows:
        batches.setdefault(item.placement_id, item.batch)
    for placement in queryset.select_related(
        "article", "article__primary_journal", "slot"
    ):
        batch = batches.get(placement.pk)
        writer.writerow(
            [
                placement.article.title,
                placement.article.static_slug,
                placement.article.primary_journal.slug,
                f"{placement.target_type}:{placement.target_slug}",
                placement.slot.code,
                placement.starts_at.isoformat() if placement.starts_at else "",
                placement.ends_at.isoformat() if placement.ends_at else "",
                placement.is_active,
                batch.batch_number if batch else "",
                batch.publish_status if batch else "",
                batch.created_by.get_username() if batch and batch.created_by else "",
                batch.executed_at.isoformat() if batch and batch.executed_at else "",
            ]
        )
    return output.getvalue()
