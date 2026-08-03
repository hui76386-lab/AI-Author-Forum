from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from django.db import transaction

from ai_author_forum.articles.category_services import (
    validate_article_category_revision,
)
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import JournalCategoryStatus
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

from .models import ArticlePlacement, LayoutSlot


@dataclass
class CategoryPlacementPlan:
    article_id: int
    revision_id: int | None
    expected: dict[int, str] = field(default_factory=dict)
    active: set[int] = field(default_factory=set)
    create: set[int] = field(default_factory=set)
    enable: set[int] = field(default_factory=set)
    disable: set[int] = field(default_factory=set)
    unchanged: set[int] = field(default_factory=set)
    errors: list[dict] = field(default_factory=list)


SYSTEM_SLOT_CODE = "category_default_listing"


def get_category_listing_slot():
    slot, _ = LayoutSlot.objects.get_or_create(
        code=SYSTEM_SLOT_CODE,
        defaults={
            "title": "栏目普通列表",
            "scope": LayoutSlot.Scope.CATEGORY,
            "max_items": 1,
            "fill_mode": LayoutSlot.FillMode.AUTO,
            "description": "System-managed automatic category listing slot.",
            "is_system": True,
            "is_active": True,
        },
    )
    changed = []
    for field_name, value in {
        "scope": LayoutSlot.Scope.CATEGORY,
        "fill_mode": LayoutSlot.FillMode.AUTO,
        "is_system": True,
        "is_active": True,
    }.items():
        if getattr(slot, field_name) != value:
            setattr(slot, field_name, value)
            changed.append(field_name)
    if changed:
        slot.save(update_fields=[*changed])
    return slot


def plan_category_placement_sync(*, article_id, revision_id=None):
    article = ArticlePage.objects.get(pk=article_id)
    assignments = _assignments(article, revision_id)
    expected = {
        item["category_id"]: "primary" if item["is_primary"] else "related"
        for item in assignments
    }
    plan = CategoryPlacementPlan(
        article_id=article_id,
        revision_id=revision_id,
        expected=expected,
    )
    existing = {
        placement.target_category_id: placement
        for placement in ArticlePlacement.objects.filter(
            article_id=article_id,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            source=ArticlePlacement.Source.SYSTEM,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
        )
    }
    plan.active = {
        category_id for category_id, item in existing.items() if item.is_active
    }
    expected_ids = set(expected)
    if not article.live or article.review_status not in {
        ArticlePage.ReviewStatus.APPROVED,
        ArticlePage.ReviewStatus.PUBLISHED,
    }:
        expected_ids = set()
        plan.expected = {}
    plan.create = expected_ids - set(existing)
    plan.enable = {
        category_id
        for category_id in expected_ids & set(existing)
        if not existing[category_id].is_active
    }
    plan.disable = {
        category_id
        for category_id, item in existing.items()
        if item.is_active and category_id not in expected_ids
    }
    plan.unchanged = expected_ids - plan.create - plan.enable
    return plan


def sync_category_placements(
    *, article_id, revision_id=None, actor=None, request_id=None
):
    """Synchronize system category placements and persist a durable result state.

    Every caller (Wagtail hooks, commands, retries, and repair jobs) goes through
    this wrapper so a failed synchronization cannot be silently downgraded.
    """
    request_id = request_id or str(uuid.uuid4())
    try:
        return _sync_category_placements_atomic(
            article_id=article_id,
            revision_id=revision_id,
            actor=actor,
            request_id=request_id,
        )
    except Exception as exc:
        error = str(exc)[:4000]
        ArticlePage.objects.filter(pk=article_id).update(
            placement_sync_status=ArticlePage.PlacementSyncStatus.FAILED,
            placement_sync_error=error,
            placement_sync_request_id=request_id,
        )
        article = ArticlePage.objects.filter(pk=article_id).first()
        record_audit_event(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.FAILURE,
            actor=actor,
            target=article,
            target_type="ArticlePage" if article is None else "",
            target_id=str(article_id) if article is None else "",
            request_id=request_id,
            message="System category placement synchronization failed",
            metadata={
                "operation": "sync_category_placements",
                "article_id": article_id,
                "revision_id": revision_id,
                "expected_category_ids": _safe_expected_category_ids(
                    article_id=article_id, revision_id=revision_id
                ),
                "error": error,
            },
        )
        raise


def _sync_category_placements_atomic(*, article_id, revision_id, actor, request_id):
    with transaction.atomic():
        article = ArticlePage.objects.select_for_update().get(pk=article_id)
        synced_revision_id = revision_id or getattr(article, "live_revision_id", None)
        plan = plan_category_placement_sync(
            article_id=article_id,
            revision_id=revision_id,
        )
        if (
            article.placement_sync_status == ArticlePage.PlacementSyncStatus.SYNCED
            and article.placement_synced_revision_id == synced_revision_id
            and not (plan.create or plan.enable or plan.disable)
        ):
            return {
                "article_id": article_id,
                "revision_id": synced_revision_id,
                "request_id": article.placement_sync_request_id,
                "created": [],
                "enabled": [],
                "disabled": [],
                "unchanged": sorted(plan.unchanged),
                "errors": [],
                "idempotent": True,
            }

        ArticlePage.objects.filter(pk=article_id).update(
            placement_sync_status=ArticlePage.PlacementSyncStatus.PENDING,
            placement_sync_error="",
            placement_sync_request_id=request_id,
        )
        slot = get_category_listing_slot()
        existing = {
            placement.target_category_id: placement
            for placement in ArticlePlacement.objects.select_for_update().filter(
                article_id=article_id,
                target_type=ArticlePlacement.TargetType.CATEGORY,
                source=ArticlePlacement.Source.SYSTEM,
                placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
            )
        }
        created = []
        enabled = []
        disabled = []
        for category_id in sorted(plan.create):
            role = plan.expected[category_id]
            placement = ArticlePlacement.objects.create(
                slot=slot,
                article=article,
                target_type=ArticlePlacement.TargetType.CATEGORY,
                target_category_id=category_id,
                target_slug="",
                placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
                source=ArticlePlacement.Source.SYSTEM,
                is_active=True,
                metadata={
                    "category_assignment_role": role,
                    "sync_source": "article_live_revision",
                    "sync_revision_id": revision_id
                    or getattr(article, "live_revision_id", None),
                    "sync_request_id": request_id,
                },
            )
            created.append(placement.pk)
        for category_id in sorted(plan.enable | plan.unchanged):
            placement = existing.get(category_id)
            if placement is None:
                continue
            role = plan.expected[category_id]
            metadata = {
                **(placement.metadata or {}),
                "category_assignment_role": role,
                "sync_source": "article_live_revision",
                "sync_revision_id": revision_id
                or getattr(article, "live_revision_id", None),
                "sync_request_id": request_id,
            }
            update = {"metadata": metadata}
            if category_id in plan.enable:
                update["is_active"] = True
                enabled.append(placement.pk)
            ArticlePlacement.objects.filter(pk=placement.pk).update(**update)
        if plan.disable:
            placements_to_disable = list(
                ArticlePlacement.objects.filter(
                    article_id=article_id,
                    target_category_id__in=plan.disable,
                    target_type=ArticlePlacement.TargetType.CATEGORY,
                    source=ArticlePlacement.Source.SYSTEM,
                    placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
                    is_active=True,
                )
            )
            for placement in placements_to_disable:
                placement.is_active = False
                placement.metadata = {
                    **(placement.metadata or {}),
                    "sync_source": "article_live_revision",
                    "sync_revision_id": revision_id
                    or getattr(article, "live_revision_id", None),
                    "sync_request_id": request_id,
                    "disabled_reason": "assignment_removed_or_article_not_production_ready",
                }
            if placements_to_disable:
                ArticlePlacement.objects.bulk_update(
                    placements_to_disable, ["is_active", "metadata", "updated_at"]
                )
                disabled.extend(item.pk for item in placements_to_disable)
        ArticlePage.objects.filter(pk=article_id).update(
            placement_sync_status=ArticlePage.PlacementSyncStatus.SYNCED,
            placement_sync_error="",
            placement_synced_revision_id=synced_revision_id,
            placement_sync_request_id=request_id,
        )
        result = {
            "article_id": article_id,
            "revision_id": synced_revision_id,
            "request_id": request_id,
            "created": created,
            "enabled": enabled,
            "disabled": disabled,
            "unchanged": sorted(plan.unchanged),
            "errors": [],
            "idempotent": False,
        }
        record_audit_event(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=article,
            request_id=request_id,
            message="System category placements synchronized",
            metadata={"operation": "sync_category_placements", **result},
        )
        changed_ids = [*created, *enabled, *disabled]
        if changed_ids:
            # Imported lazily to keep the article/category synchronization path
            # independent from the publisher's consistency validator at startup.
            from ai_author_forum.static_publish.automatic import queue_placement_publish

            queue_placement_publish(
                *ArticlePlacement.objects.filter(pk__in=changed_ids).select_related(
                    "article"
                ),
                actor=actor,
                reason="category_placement_sync",
            )
        return result


def disable_category_placements(
    *, article_id=None, category_id=None, actor=None, request_id=None
):
    if article_id is None and category_id is None:
        raise ValueError("article_id or category_id is required")
    request_id = request_id or str(uuid.uuid4())
    with transaction.atomic():
        queryset = ArticlePlacement.objects.select_for_update().filter(
            target_type=ArticlePlacement.TargetType.CATEGORY,
            source=ArticlePlacement.Source.SYSTEM,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
            is_active=True,
        )
        if article_id is not None:
            queryset = queryset.filter(article_id=article_id)
        if category_id is not None:
            queryset = queryset.filter(target_category_id=category_id)
        placements = list(queryset)
        for placement in placements:
            placement.is_active = False
            placement.metadata = {
                **(placement.metadata or {}),
                "sync_source": "article_unpublish_or_category_disable",
                "sync_request_id": request_id,
                "disabled_reason": (
                    "article_unpublished"
                    if article_id is not None
                    else "category_disabled"
                ),
            }
        if placements:
            ArticlePlacement.objects.bulk_update(
                placements, ["is_active", "metadata", "updated_at"]
            )
        article = None
        category = None
        if article_id is not None:
            article = ArticlePage.objects.filter(pk=article_id).first()
            ArticlePage.objects.filter(pk=article_id).update(
                placement_sync_status=ArticlePage.PlacementSyncStatus.SYNCED,
                placement_sync_error="",
                placement_synced_revision_id=getattr(article, "live_revision_id", None),
                placement_sync_request_id=request_id,
            )
        if category_id is not None:
            from ai_author_forum.journals.models import JournalCategory

            category = JournalCategory.objects.filter(pk=category_id).first()
        result = {
            "article_id": article_id,
            "category_id": category_id,
            "request_id": request_id,
            "disabled": len(placements),
            "placement_ids": [item.pk for item in placements],
        }
        record_audit_event(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=article or category,
            target_type="ArticlePage" if article_id is not None else "JournalCategory",
            target_id=str(article_id if article_id is not None else category_id),
            request_id=request_id,
            message="System category placements disabled",
            metadata={"operation": "disable_category_placements", **result},
        )
        return result


def repair_category_placement_drift(
    *, journal_id=None, article_ids=None, dry_run=True, actor=None
):
    queryset = ArticlePage.objects.all().order_by("pk")
    if journal_id is not None:
        queryset = queryset.filter(primary_journal_id=journal_id)
    if article_ids is not None:
        queryset = queryset.filter(pk__in=article_ids)
    plans = []
    results = []
    for article_id in queryset.values_list("pk", flat=True).iterator(chunk_size=200):
        plan = plan_category_placement_sync(article_id=article_id)
        if plan.create or plan.enable or plan.disable:
            plans.append(plan)
            if not dry_run:
                results.append(
                    sync_category_placements(article_id=article_id, actor=actor)
                )
    return {
        "dry_run": dry_run,
        "drift_count": len(plans),
        "article_ids": [plan.article_id for plan in plans],
        "results": results,
    }


def validate_category_placement_consistency(*, article_ids=None):
    queryset = ArticlePage.objects.all()
    if article_ids is not None:
        queryset = queryset.filter(pk__in=article_ids)
    errors = []
    for article_id in queryset.values_list("pk", flat=True):
        plan = plan_category_placement_sync(article_id=article_id)
        if plan.create or plan.enable or plan.disable:
            errors.append(
                {
                    "code": "CATEGORY_PUBLICATION_DRIFT",
                    "article_id": article_id,
                    "create": sorted(plan.create),
                    "enable": sorted(plan.enable),
                    "disable": sorted(plan.disable),
                }
            )
    return errors


def _assignments(article, revision_id=None):
    if revision_id:
        revision = article.revisions.get(pk=revision_id)
        revision_object = revision.as_object()
        payload = [
            {"category_id": item.category_id, "is_primary": item.is_primary}
            for item in revision_object.category_assignments.all()
        ]
        validate_article_category_revision(
            article=revision_object,
            revision_content={"category_assignments": payload},
            action="submit",
        )
        return payload
    source = article.live_revision.as_object() if article.live_revision_id else article
    return [
        {"category_id": item.category_id, "is_primary": item.is_primary}
        for item in source.category_assignments.select_related("category").all()
        if item.category.status
        in {JournalCategoryStatus.ACTIVE, JournalCategoryStatus.HIDDEN}
    ]


def _safe_expected_category_ids(*, article_id, revision_id=None):
    try:
        article = ArticlePage.objects.get(pk=article_id)
        return sorted(
            item["category_id"] for item in _assignments(article, revision_id)
        )
    except Exception:
        return []
