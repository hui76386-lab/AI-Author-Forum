from __future__ import annotations

from collections import defaultdict

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from .models import ArticlePlacement, LayoutSlot, PlacementBatch, PlacementBatchItem
from .publishing import create_batch_publish
from .selectors import mark_journal_used
from .services import (
    PLACEABLE_REVIEW_STATUSES,
    has_placement_permission,
    require_placement_scope,
)


class BatchValidationError(ValidationError):
    """Validation failure that preserves item-level errors for the review page."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(
            "; ".join(error["message"] for error in errors)
            or "Batch validation failed."
        )


def snapshot_placement(placement):
    return {
        "placement_id": placement.pk,
        "article_id": placement.article_id,
        "article_static_slug": placement.article.static_slug,
        "slot": placement.slot.code,
        "target_type": placement.target_type,
        "target_slug": placement.target_slug,
        "target_category_id": placement.target_category_id,
        "is_active": placement.is_active,
        "is_pinned": placement.is_pinned,
        "sort_order": placement.sort_order,
        "starts_at": placement.starts_at.isoformat() if placement.starts_at else None,
        "ends_at": placement.ends_at.isoformat() if placement.ends_at else None,
    }


def create_draft(*, actor, mode, operation=PlacementBatch.Operation.CREATE, **values):
    if not has_placement_permission(actor, "add"):
        raise PermissionDenied
    return PlacementBatch.objects.create(
        mode=mode,
        operation=operation,
        strict_mode=True,
        created_by=actor,
        updated_by=actor,
        **values,
    )


@transaction.atomic
def update_draft(batch, *, actor, step=None, selected_article_ids=None, **values):
    if batch.is_executed:
        raise ValidationError("Executed batches cannot be changed.")
    if (
        batch.created_by_id
        and batch.created_by_id != actor.pk
        and not is_super_admin(actor)
    ):
        raise PermissionDenied
    for name, value in values.items():
        if name in {
            "mode",
            "operation",
            "target_type",
            "target_slug",
            "target_category",
            "slot",
            "starts_at",
            "ends_at",
            "is_pinned",
            "options",
        }:
            setattr(batch, name, value)
    if step:
        batch.current_step = step
    batch.updated_by = actor
    batch.full_clean()
    batch.save()
    if selected_article_ids is not None:
        replace_draft_articles(batch, selected_article_ids, actor=actor)
    require_batch_scope(batch, actor)
    return batch


def replace_draft_articles(batch, article_ids, *, actor):
    ids = [int(pk) for pk in article_ids]
    if len(ids) != len(set(ids)):
        raise ValidationError("An article can only be selected once per batch.")
    limit = max(1, int(getattr(settings, "PLACEMENTS_BATCH_MAX_ITEMS", 100)))
    if len(ids) > limit:
        raise ValidationError(f"A batch may contain at most {limit} articles.")
    articles = list(
        ArticlePage.objects.filter(pk__in=ids).select_related("primary_journal")
    )
    if len(articles) != len(ids):
        raise ValidationError("One or more selected articles do not exist.")
    for article in articles:
        require_placement_scope(
            actor,
            ArticlePlacement(
                article=article,
                target_type=batch.target_type,
                target_slug=batch.target_slug,
                target_category=batch.target_category,
                slot=batch.slot,
                source=ArticlePlacement.Source.MANUAL,
            ),
            action="add",
        )
    by_id = {article.pk: article for article in articles}
    batch.items.exclude(article_id__in=ids).delete()
    existing = {
        item.article_id: item for item in batch.items.filter(article_id__in=ids)
    }
    for index, article_id in enumerate(ids, start=1):
        item = existing.get(article_id)
        if item:
            item.sort_order = index * 10
            item.validation_status = PlacementBatchItem.ValidationStatus.PENDING
            item.execution_status = PlacementBatchItem.ExecutionStatus.PENDING
            item.error_code = ""
            item.error_message = ""
            item.save(
                update_fields=(
                    "sort_order",
                    "validation_status",
                    "execution_status",
                    "error_code",
                    "error_message",
                    "updated_at",
                )
            )
        else:
            PlacementBatchItem.objects.create(
                batch=batch, article=by_id[article_id], sort_order=index * 10
            )


def _date_error(batch):
    if batch.starts_at and batch.ends_at and batch.ends_at <= batch.starts_at:
        return "The end time must be later than the start time."
    return None


def _target_journal(batch):
    if batch.target_type != ArticlePlacement.TargetType.JOURNAL:
        return None
    return Journal.objects.filter(
        slug=batch.target_slug, status=JournalStatus.ACTIVE
    ).first()


def require_batch_scope(batch, actor):
    if is_super_admin(actor):
        return
    items = batch.items.select_related("article", "article__primary_journal")
    if not items.exists():
        if batch.target_type != ArticlePlacement.TargetType.JOURNAL:
            raise PermissionDenied("空白投放草稿必须先选择本刊目标。")
        journal = _target_journal(batch)
        if journal is None:
            raise PermissionDenied("目标子期刊不存在或不可访问。")
        from ai_author_forum.journals.models import JournalEditorAssignment

        if (
            not JournalEditorAssignment.objects.effective()
            .filter(
                user=actor,
                journal=journal,
                role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            )
            .exists()
        ):
            raise PermissionDenied("无权管理该子期刊投放。")
        return
    for item in items:
        require_placement_scope(
            actor,
            ArticlePlacement(
                article=item.article,
                target_type=batch.target_type,
                target_slug=batch.target_slug,
                target_category=batch.target_category,
                slot=batch.slot,
                source=ArticlePlacement.Source.MANUAL,
            ),
            action="add",
        )


def _image_file_is_available(image):
    """Return whether an image still has a readable original source file."""
    image_file = getattr(image, "file", None)
    if not image_file or not image_file.name:
        return False
    try:
        return image_file.storage.exists(image_file.name)
    except (OSError, ValueError):
        return False


def _draft_errors(batch, *, lock=False):
    batch = PlacementBatch.objects.select_related("slot", "target_category").get(
        pk=batch.pk
    )
    items = list(
        batch.items.select_related(
            "article", "article__primary_journal", "article__featured_image"
        ).order_by("sort_order", "pk")
    )
    errors = []
    if not items:
        errors.append(
            {
                "item_id": None,
                "code": "empty",
                "message": "Select at least one article.",
            }
        )
    if not batch.slot_id:
        errors.append(
            {
                "item_id": None,
                "code": "slot_required",
                "message": "Select a fixed slot.",
            }
        )
    elif not batch.slot.is_active:
        errors.append(
            {
                "item_id": None,
                "code": "slot_inactive",
                "message": "The selected slot is inactive.",
            }
        )
    if (
        batch.mode == PlacementBatch.Mode.BULK_CREATE
        and batch.target_type != ArticlePlacement.TargetType.JOURNAL
    ):
        errors.append(
            {
                "item_id": None,
                "code": "journal_target_required",
                "message": "Bulk placement requires one journal target.",
            }
        )
    journal = _target_journal(batch)
    if batch.target_type == ArticlePlacement.TargetType.JOURNAL and journal is None:
        errors.append(
            {
                "item_id": None,
                "code": "journal_inactive",
                "message": "The target journal does not exist or is inactive.",
            }
        )
    date_error = _date_error(batch)
    if date_error:
        errors.append({"item_id": None, "code": "time_range", "message": date_error})
    options = batch.options or {}
    override_image_id = options.get("override_image_id")
    override_image = None
    if override_image_id not in (None, ""):
        try:
            override_image_id = int(override_image_id)
        except (TypeError, ValueError):
            errors.append(
                {
                    "item_id": None,
                    "code": "override_image",
                    "message": "Choose a valid override image.",
                }
            )
        else:
            override_image = (
                CustomImage.objects.filter(pk=override_image_id)
                .only("pk", "file")
                .first()
            )
            if override_image is None:
                errors.append(
                    {
                        "item_id": None,
                        "code": "override_image",
                        "message": (
                            "已选覆盖图片已被删除，无法投放。请返回展示设置，"
                            "重新选择图片，或清除覆盖图片后使用文章封面。"
                        ),
                    }
                )
            elif not _image_file_is_available(override_image):
                errors.append(
                    {
                        "item_id": None,
                        "code": "override_image",
                        "message": (
                            "覆盖图片的原始文件已丢失，无法投放。请返回展示设置，"
                            "重新选择可用图片，或清除覆盖图片后使用文章封面。"
                        ),
                    }
                )
    if batch.slot_id:
        expected_scope = {
            ArticlePlacement.TargetType.MAIN_SITE: LayoutSlot.Scope.HOME,
            ArticlePlacement.TargetType.SECTION: LayoutSlot.Scope.SECTION,
            ArticlePlacement.TargetType.JOURNAL: LayoutSlot.Scope.JOURNAL,
            ArticlePlacement.TargetType.ARTICLE: LayoutSlot.Scope.ARTICLE,
            ArticlePlacement.TargetType.SEARCH: LayoutSlot.Scope.SEARCH,
            ArticlePlacement.TargetType.CATEGORY: LayoutSlot.Scope.CATEGORY,
        }.get(batch.target_type)
        if expected_scope and batch.slot.scope != expected_scope:
            errors.append(
                {
                    "item_id": None,
                    "code": "slot_scope",
                    "message": "The selected slot does not match the target type.",
                }
            )

    valid_items = []
    for item in items:
        article = item.article
        if article.review_status not in PLACEABLE_REVIEW_STATUSES:
            errors.append(
                {
                    "item_id": item.pk,
                    "code": "article_review",
                    "message": f"{article.title}: article is not approved.",
                }
            )
        elif (
            not article.primary_journal_id
            or article.primary_journal.status != JournalStatus.ACTIVE
        ):
            errors.append(
                {
                    "item_id": item.pk,
                    "code": "article_journal",
                    "message": f"{article.title}: source journal is inactive.",
                }
            )
        elif journal and not (
            article.primary_journal_id == journal.pk
            or article.related_journals.filter(pk=journal.pk).exists()
        ):
            errors.append(
                {
                    "item_id": item.pk,
                    "code": "journal_membership",
                    "message": f"{article.title}: article does not belong to the target journal.",
                }
            )
        elif (
            override_image is not None
            and not (options.get("override_image_alt") or "").strip()
        ):
            errors.append(
                {
                    "item_id": item.pk,
                    "code": "image_alt",
                    "message": (
                        f"{article.title}: 覆盖图片缺少图片说明（Alt），无法投放。"
                        "请填写图片说明后重新复核。"
                    ),
                }
            )
        elif override_image is None and article.featured_image_id:
            if not _image_file_is_available(article.featured_image):
                errors.append(
                    {
                        "item_id": item.pk,
                        "code": "article_image",
                        "message": (
                            f"{article.title}: 文章封面的原始文件已丢失，无法投放。"
                            "请选择一张可用的覆盖图片。"
                        ),
                    }
                )
            elif not (article.featured_image_alt or "").strip():
                errors.append(
                    {
                        "item_id": item.pk,
                        "code": "image_alt",
                        "message": (
                            f"{article.title}: 当前将使用文章封面，但封面缺少图片说明（Alt），"
                            "无法投放。取消覆盖图片并不需要填写覆盖图片说明；"
                            "请在文章编辑中补充封面说明，或选择/上传一张覆盖图片并填写说明。"
                        ),
                    }
                )
            else:
                valid_items.append(item)
        else:
            valid_items.append(item)

    if errors or not batch.slot_id:
        return items, errors
    peers = ArticlePlacement.objects.filter(
        slot=batch.slot,
        target_type=batch.target_type,
        target_slug=batch.target_slug,
        target_category=batch.target_category,
        is_active=True,
        source=ArticlePlacement.Source.MANUAL,
    )
    if batch.ends_at:
        peers = peers.filter(Q(starts_at__isnull=True) | Q(starts_at__lt=batch.ends_at))
    if batch.starts_at:
        peers = peers.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=batch.starts_at))
    if lock:
        peers = peers.select_for_update()
    peer_rows = list(peers.select_related("article", "slot"))
    peer_by_article = {placement.article_id: placement for placement in peer_rows}
    for item in valid_items:
        duplicate = peer_by_article.get(item.article_id)
        if duplicate:
            errors.append(
                {
                    "item_id": item.pk,
                    "code": "duplicate",
                    "message": f"{item.article.title}: already has overlapping placement #{duplicate.pk}.",
                }
            )

    # Only article selections without a conflicting active record change capacity.
    activation_count = sum(
        1 for item in valid_items if item.article_id not in peer_by_article
    )
    if len(peer_rows) + activation_count > batch.slot.max_items:
        errors.append(
            {
                "item_id": None,
                "code": "capacity",
                "message": f"Slot {batch.slot.code} has capacity {batch.slot.max_items}; execution would require {len(peer_rows) + activation_count}.",
            }
        )
    return items, errors


def _persist_validation(items, errors):
    by_item = defaultdict(list)
    for error in errors:
        if error["item_id"]:
            by_item[error["item_id"]].append(error)
    for item in items:
        item_errors = by_item.get(item.pk, [])
        item.validation_status = (
            PlacementBatchItem.ValidationStatus.FAILED
            if item_errors
            else PlacementBatchItem.ValidationStatus.PASSED
        )
        item.error_code = item_errors[0]["code"] if item_errors else ""
        item.error_message = "\n".join(error["message"] for error in item_errors)
        item.save(
            update_fields=(
                "validation_status",
                "error_code",
                "error_message",
                "updated_at",
            )
        )


def precheck_batch(batch, *, actor):
    if (
        batch.created_by_id
        and batch.created_by_id != actor.pk
        and not is_super_admin(actor)
    ):
        raise PermissionDenied
    batch = PlacementBatch.objects.get(pk=batch.pk)
    require_batch_scope(batch, actor)
    if batch.is_executed:
        return {
            "batch": batch,
            "items": list(batch.items.all()),
            "errors": [
                {
                    "item_id": None,
                    "code": "batch_executed",
                    "message": (
                        "This placement batch has already finished. "
                        "Open the execution result instead of running it again."
                    ),
                }
            ],
            "ok": False,
        }
    batch.status = PlacementBatch.Status.VALIDATING
    batch.updated_by = actor
    batch.save(update_fields=("status", "updated_by", "updated_at"))
    items, errors = _draft_errors(batch)
    _persist_validation(items, errors)
    batch.status = (
        PlacementBatch.Status.FAILED if errors else PlacementBatch.Status.READY
    )
    batch.failure_count = len(errors)
    batch.updated_by = actor
    batch.save(update_fields=("status", "failure_count", "updated_by", "updated_at"))
    return {"batch": batch, "items": items, "errors": errors, "ok": not errors}


def _audit_metadata(batch, *, before, after, ip_address, publish_job=None):
    return {
        "batch_id": str(batch.pk),
        "batch_number": batch.batch_number,
        "mode": batch.mode,
        "operation": batch.operation,
        "target_type": batch.target_type,
        "target_slug": batch.target_slug,
        "slot": batch.slot.code if batch.slot_id else "",
        "count": len(after),
        "before": before,
        "after": after,
        "publish_job_id": publish_job.pk if publish_job else None,
        "publish_status": batch.publish_status,
        "ip_address": ip_address,
    }


def execute_create_batch(batch, *, actor, ip_address=None):
    """Strictly create/reactivate every item or create none of them."""
    if not has_placement_permission(actor, "add"):
        raise PermissionDenied
    try:
        with transaction.atomic():
            batch = PlacementBatch.objects.select_for_update().get(pk=batch.pk)
            require_batch_scope(batch, actor)
            if batch.is_executed:
                raise ValidationError("This batch has already been executed.")
            batch.status = PlacementBatch.Status.EXECUTING
            batch.updated_by = actor
            batch.save(update_fields=("status", "updated_by", "updated_at"))
            batch.slot = LayoutSlot.objects.select_for_update().get(pk=batch.slot_id)
            items, errors = _draft_errors(batch, lock=True)
            if errors:
                raise BatchValidationError(errors)
            locked_items = list(
                batch.items.select_for_update(of=("self",))
                .select_related("article", "article__primary_journal")
                .order_by("sort_order", "pk")
            )
            existing = {
                placement.article_id: placement
                for placement in ArticlePlacement.objects.select_for_update()
                .select_related("article", "slot")
                .filter(
                    slot=batch.slot,
                    target_type=batch.target_type,
                    target_slug=batch.target_slug,
                    target_category=batch.target_category,
                    source=ArticlePlacement.Source.MANUAL,
                    article_id__in=[item.article_id for item in locked_items],
                )
            }
            next_sort = (
                ArticlePlacement.objects.filter(
                    slot=batch.slot,
                    target_type=batch.target_type,
                    target_slug=batch.target_slug,
                    target_category=batch.target_category,
                    source=ArticlePlacement.Source.MANUAL,
                ).aggregate(value=Max("sort_order"))["value"]
                or 0
            )
            before, after, events = [], [], []
            created_count = reactivated_count = 0
            for item in locked_items:
                placement = existing.get(item.article_id)
                if placement is None:
                    next_sort += 10
                    placement = ArticlePlacement(
                        article=item.article,
                        slot=batch.slot,
                        target_type=batch.target_type,
                        target_slug=batch.target_slug,
                        target_category=batch.target_category,
                        source=ArticlePlacement.Source.MANUAL,
                        sort_order=next_sort,
                    )
                    execution = PlacementBatchItem.ExecutionStatus.CREATED
                    created_count += 1
                else:
                    before_snapshot = snapshot_placement(placement)
                    before.append(before_snapshot)
                    placement.starts_at = batch.starts_at
                    placement.ends_at = batch.ends_at
                    placement.is_pinned = batch.is_pinned
                    placement.is_active = True
                    if not placement.sort_order:
                        next_sort += 10
                        placement.sort_order = next_sort
                    execution = PlacementBatchItem.ExecutionStatus.UPDATED
                    reactivated_count += 1
                placement.starts_at = batch.starts_at
                placement.ends_at = batch.ends_at
                placement.is_pinned = batch.is_pinned
                placement.is_active = True
                if batch.mode == PlacementBatch.Mode.SINGLE:
                    placement.override_title = (batch.options or {}).get(
                        "override_title", ""
                    )
                    placement.override_summary = (batch.options or {}).get(
                        "override_summary", ""
                    )
                    placement.override_image_alt = (batch.options or {}).get(
                        "override_image_alt", ""
                    )
                    override_image_id = (batch.options or {}).get("override_image_id")
                    placement.override_image_id = override_image_id or None
                placement.full_clean()
                placement.save()
                after_snapshot = snapshot_placement(placement)
                after.append(after_snapshot)
                events.append(placement)
                item.placement = placement
                item.before_snapshot = (
                    before_snapshot if placement.article_id in existing else {}
                )
                item.after_snapshot = after_snapshot
                item.validation_status = PlacementBatchItem.ValidationStatus.PASSED
                item.execution_status = execution
                item.error_code = ""
                item.error_message = ""
                item.save()
            job, publish_status = create_batch_publish(
                batch=batch, events=[*before, *after], actor=actor
            )
            batch.status = PlacementBatch.Status.SUCCEEDED
            batch.executed_at = timezone.now()
            batch.updated_by = actor
            batch.success_count = created_count + reactivated_count
            batch.failure_count = 0
            batch.skipped_count = 0
            batch.publish_job = job
            batch.publish_status = publish_status
            batch.save()
            if batch.target_type == ArticlePlacement.TargetType.JOURNAL:
                journal = Journal.objects.get(slug=batch.target_slug)
                mark_journal_used(user=actor, journal=journal)
            AuditLog.record(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.SUCCESS,
                actor=actor,
                target=batch,
                message="Placement batch executed.",
                metadata=_audit_metadata(
                    batch,
                    before=before,
                    after=after,
                    ip_address=ip_address,
                    publish_job=job,
                ),
                ip_address=ip_address,
            )
            return batch
    except BatchValidationError as exc:
        batch.refresh_from_db()
        batch.status = PlacementBatch.Status.FAILED
        batch.failure_count = len(exc.errors)
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
        _persist_validation(list(batch.items.all()), exc.errors)
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.FAILURE,
            actor=actor,
            target=batch,
            message="Placement batch execution blocked by strict validation.",
            metadata={
                "batch_id": str(batch.pk),
                "batch_number": batch.batch_number,
                "errors": exc.errors,
                "ip_address": ip_address,
            },
            ip_address=ip_address,
        )
        raise
    except Exception as exc:
        batch.refresh_from_db()
        batch.status = PlacementBatch.Status.FAILED
        batch.failure_count = max(1, batch.items.count())
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
            message="Placement batch execution failed and was rolled back.",
            metadata={
                "batch_id": str(batch.pk),
                "batch_number": batch.batch_number,
                "error": str(exc),
                "ip_address": ip_address,
            },
            ip_address=ip_address,
        )
        raise
