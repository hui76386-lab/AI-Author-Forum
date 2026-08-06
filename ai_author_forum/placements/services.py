from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q

from ai_author_forum.articles.models import ArticlePage, ArticleReviewRecord
from ai_author_forum.articles.services import get_approved_articles
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.access_control import (
    can_maintain_placement_target,
    can_manage_placement_target,
    get_journal_editor_assignment,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.automatic import (
    create_pending_placement_publish,
    queue_placement_publish,
)

from .models import ArticlePlacement, LayoutSlot, normalize_target_slug
from .publishing import can_publish_automatically

PLACEABLE_REVIEW_STATUSES = (
    ArticlePage.ReviewStatus.APPROVED,
    ArticlePage.ReviewStatus.PUBLISHED,
)

HOME_SLOT_LIMITS = {
    "home_hero": 1,
    "home_visual_stories": 2,
    "home_featured": 5,
    "latest_ai_article": 8,
}
HOME_PRIMARY_SLOT_CODES = tuple(HOME_SLOT_LIMITS)

SECTION_ARTICLE_TYPES = {
    "ai-article": ArticlePage.ArticleType.AI_ARTICLE,
    "news": ArticlePage.ArticleType.NEWS,
    "opinion": ArticlePage.ArticleType.OPINION,
    "research-analysis": ArticlePage.ArticleType.RESEARCH_ANALYSIS,
}

_COUNT_SENTINEL = object()


class SlotItemList(list):
    def count(self, value=_COUNT_SENTINEL):
        if value is _COUNT_SENTINEL:
            return len(self)
        return super().count(value)


def has_placement_permission(user, action):
    """Enforce manual-placement permissions consistently in UI and services."""
    if is_super_admin(user):
        return True
    if not user or not user.is_active:
        return False
    assignments = JournalEditorAssignment.objects.effective().filter(user=user)
    if action == "add":
        return assignments.filter(
            role=JournalEditorAssignment.Role.CHIEF_EDITOR
        ).exists()
    return any(
        assignment.role
        in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
        or (
            assignment.role == JournalEditorAssignment.Role.ASSOCIATE_EDITOR
            and JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
            in (assignment.responsibilities or [])
        )
        for assignment in assignments
    )


def _placement_target(placement):
    if placement.target_type == ArticlePlacement.TargetType.JOURNAL:
        return Journal.objects.filter(slug=placement.target_slug).first()
    if placement.target_type == ArticlePlacement.TargetType.CATEGORY:
        return placement.target_category
    if placement.target_type == ArticlePlacement.TargetType.ARTICLE:
        return ArticlePage.objects.filter(static_slug=placement.target_slug).first()
    return None


def require_placement_scope(actor, placement, *, action="change"):
    target = _placement_target(placement)
    checker = (
        can_manage_placement_target
        if action == "add"
        else can_maintain_placement_target
    )
    if not checker(actor, placement.article, placement.target_type, target):
        raise PermissionDenied("无权操作该投放目标。")
    if action != "add" and not is_super_admin(actor):
        assignment = get_journal_editor_assignment(
            actor, placement.article.primary_journal
        )
        if (
            assignment
            and assignment.role != JournalEditorAssignment.Role.CHIEF_EDITOR
            and (not placement.pk or placement.article.last_static_published_at is None)
        ):
            raise PermissionDenied("副编辑只能维护已经完成过静态发布的文章。")


def overlapping_placements(placement, *, lock=False):
    """Return active peer placements whose half-open schedules overlap."""
    queryset = ArticlePlacement.objects.filter(
        slot_id=placement.slot_id,
        target_type=placement.target_type,
        target_slug=normalize_target_slug(placement.target_slug),
        target_category_id=placement.target_category_id,
        is_active=True,
        source=ArticlePlacement.Source.MANUAL,
    )
    if placement.pk:
        queryset = queryset.exclude(pk=placement.pk)
    if placement.ends_at is not None:
        queryset = queryset.filter(
            Q(starts_at__isnull=True) | Q(starts_at__lt=placement.ends_at)
        )
    if placement.starts_at is not None:
        queryset = queryset.filter(
            Q(ends_at__isnull=True) | Q(ends_at__gt=placement.starts_at)
        )
    if lock:
        queryset = queryset.select_for_update()
    return queryset


def placement_capacity(placement):
    """Return max/current/remaining capacity for the candidate schedule."""
    if not placement.slot_id:
        return {"max_items": 0, "current": 0, "remaining": 0}
    current = overlapping_placements(placement).count()
    maximum = placement.slot.max_items
    return {
        "max_items": maximum,
        "current": current,
        "remaining": max(0, maximum - current),
    }


def validate_placement_schedule(placement, *, lock=False):
    if (
        placement.starts_at
        and placement.ends_at
        and placement.ends_at <= placement.starts_at
    ):
        raise ValidationError({"ends_at": "失效时间必须晚于生效时间。"})
    if not placement.is_active or not placement.slot_id:
        return
    peers = overlapping_placements(placement, lock=lock)
    duplicate = peers.filter(article_id=placement.article_id).first()
    if duplicate is not None:
        raise ValidationError(
            {
                "article": (
                    f"该文章与投放 #{duplicate.pk} 在同一目标、同一版位的生效时间区间冲突。"
                )
            }
        )
    if (
        placement.target_type == ArticlePlacement.TargetType.MAIN_SITE
        and placement.slot.code in HOME_PRIMARY_SLOT_CODES
        and placement.article_id
    ):
        homepage_peers = ArticlePlacement.objects.filter(
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
            target_slug="",
            article_id=placement.article_id,
            slot__code__in=HOME_PRIMARY_SLOT_CODES,
            is_active=True,
            source=ArticlePlacement.Source.MANUAL,
        )
        if placement.pk:
            homepage_peers = homepage_peers.exclude(pk=placement.pk)
        if placement.ends_at is not None:
            homepage_peers = homepage_peers.filter(
                Q(starts_at__isnull=True) | Q(starts_at__lt=placement.ends_at)
            )
        if placement.starts_at is not None:
            homepage_peers = homepage_peers.filter(
                Q(ends_at__isnull=True) | Q(ends_at__gt=placement.starts_at)
            )
        if lock:
            homepage_peers = homepage_peers.select_for_update()
        homepage_duplicate = homepage_peers.select_related("slot").first()
        if homepage_duplicate is not None:
            raise ValidationError(
                {
                    "article": (
                        f"该文章已在首页其他主要版位的重叠时间窗中投放，首页主要版位不允许重复。 "
                        f"({homepage_duplicate.slot.code}, #{homepage_duplicate.pk})"
                    )
                }
            )
    if peers.count() >= placement.slot.max_items:
        raise ValidationError(
            {
                "slot": (
                    f"版位 {placement.slot.code} 在该时间区间最多允许 "
                    f"{placement.slot.max_items} 条启用投放，当前容量已满。"
                )
            }
        )


def _placement_audit_metadata(placement):
    return {
        "placement_id": placement.pk,
        "article_id": placement.article_id,
        "article_static_slug": placement.article.static_slug,
        "slot": placement.slot.code,
        "target_type": placement.target_type,
        "target_slug": placement.target_slug,
        "target_category_id": placement.target_category_id,
        "override_title": placement.override_title,
        "override_summary": placement.override_summary,
        "override_image_id": placement.override_image_id,
        "override_image_alt": placement.override_image_alt,
        "is_pinned": placement.is_pinned,
        "sort_order": placement.sort_order,
        "starts_at": placement.starts_at.isoformat() if placement.starts_at else None,
        "ends_at": placement.ends_at.isoformat() if placement.ends_at else None,
        "is_active": placement.is_active,
    }


def _queue_or_request_placement_publish(*events, actor):
    """Automatically publish only an authorized platform or journal-scoped change."""
    if can_publish_automatically(actor, events):
        queue_placement_publish(*events, actor=actor)
        return None
    return create_pending_placement_publish(*events, actor=actor)


def _publish_audit_metadata(publish_job):
    return {
        "publish_job_id": publish_job.pk if publish_job else None,
        "publish_status": (
            "pending_publisher_approval" if publish_job else "automatic_publish"
        ),
    }


def save_manual_placement(placement, *, actor, ip_address=None):
    """Create/update a manual placement under a locked slot and write one audit."""
    action = "change" if placement.pk else "add"
    if not has_placement_permission(actor, action):
        raise PermissionDenied
    if placement.source != ArticlePlacement.Source.MANUAL:
        raise ValidationError("投放工作台只能维护人工投放。")
    before = None
    with transaction.atomic():
        slot = LayoutSlot.objects.select_for_update().get(pk=placement.slot_id)
        placement.slot = slot
        if placement.pk:
            current = ArticlePlacement.objects.select_for_update().get(pk=placement.pk)
            if current.source != ArticlePlacement.Source.MANUAL:
                raise PermissionDenied
            before = _placement_audit_metadata(current)
        require_placement_scope(actor, placement, action=action)
        validate_placement_schedule(placement, lock=True)
        placement.full_clean()
        placement.save()
        after = _placement_audit_metadata(placement)
        publish_job = _queue_or_request_placement_publish(before, after, actor=actor)
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=placement,
            message="更新文章投放配置。" if before else "创建文章投放配置。",
            metadata={
                "before": before,
                "after": after,
                **_publish_audit_metadata(publish_job),
            },
            ip_address=ip_address,
        )
        placement.pending_publish_job = publish_job
    return placement


def deactivate_manual_placement(placement_id, *, actor, ip_address=None):
    if not has_placement_permission(actor, "change"):
        raise PermissionDenied
    with transaction.atomic():
        placement = (
            ArticlePlacement.objects.select_for_update()
            .select_related("article", "slot")
            .get(pk=placement_id, source=ArticlePlacement.Source.MANUAL)
        )
        require_placement_scope(actor, placement)
        before = _placement_audit_metadata(placement)
        placement.is_active = False
        placement.save(update_fields=("is_active", "updated_at"))
        after = _placement_audit_metadata(placement)
        publish_job = _queue_or_request_placement_publish(before, after, actor=actor)
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=placement,
            message="停用文章投放。",
            metadata={
                "before": before,
                "after": after,
                **_publish_audit_metadata(publish_job),
            },
            ip_address=ip_address,
        )
        placement.pending_publish_job = publish_job
    return placement


def reorder_placements(placement_ids, *, actor, ip_address=None):
    """Reorder one target+slot group using a transaction and row locks."""
    if not has_placement_permission(actor, "change"):
        raise PermissionDenied
    ordered_ids = [int(value) for value in placement_ids]
    if not ordered_ids or len(ordered_ids) != len(set(ordered_ids)):
        raise ValidationError("排序列表不能为空且不能包含重复投放。")
    with transaction.atomic():
        placements = list(
            ArticlePlacement.objects.select_for_update()
            .select_related("slot", "article")
            .filter(pk__in=ordered_ids, source=ArticlePlacement.Source.MANUAL)
        )
        if len(placements) != len(ordered_ids):
            raise ValidationError("排序列表包含不存在或无权操作的投放。")
        for placement in placements:
            require_placement_scope(actor, placement)
        first = placements[0]
        group_key = (
            first.slot_id,
            first.target_type,
            first.target_slug,
            first.target_category_id,
        )
        if any(
            (p.slot_id, p.target_type, p.target_slug, p.target_category_id) != group_key
            for p in placements
        ):
            raise ValidationError("只能调整同一目标、同一版位内的投放顺序。")
        LayoutSlot.objects.select_for_update().get(pk=first.slot_id)
        complete_group = list(
            ArticlePlacement.objects.select_for_update()
            .filter(
                slot_id=first.slot_id,
                target_type=first.target_type,
                target_slug=first.target_slug,
                target_category_id=first.target_category_id,
                source=ArticlePlacement.Source.MANUAL,
                is_active=True,
            )
            .order_by("-is_pinned", "sort_order", "pk")
        )
        complete_ids = {p.pk for p in complete_group}
        if set(ordered_ids) != complete_ids:
            raise ValidationError("排序必须包含该目标版位内全部启用投放。")
        before = [p.pk for p in complete_group]
        by_id = {p.pk: p for p in placements}
        for index, placement_id in enumerate(ordered_ids, start=1):
            by_id[placement_id].sort_order = index * 10
        ArticlePlacement.objects.bulk_update(placements, ("sort_order",))
        publish_job = _queue_or_request_placement_publish(*placements, actor=actor)
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=first.slot,
            message="调整同目标同版位投放顺序。",
            metadata={
                "target_type": first.target_type,
                "target_slug": first.target_slug,
                "target_category_id": first.target_category_id,
                "before": before,
                "after": ordered_ids,
                **_publish_audit_metadata(publish_job),
            },
            ip_address=ip_address,
        )
        for placement in placements:
            placement.pending_publish_job = publish_job
    return [by_id[placement_id] for placement_id in ordered_ids]


def bulk_place_articles_in_journal(
    *,
    articles,
    journal,
    slot,
    actor,
    starts_at=None,
    ends_at=None,
    is_pinned=False,
    ip_address=None,
):
    """Create or reactivate one batch of manual placements for one journal slot."""
    if not has_placement_permission(actor, "add"):
        raise PermissionDenied

    selected_articles = list(dict.fromkeys(articles))
    if not selected_articles:
        raise ValidationError("请至少选择一篇文章。")

    with transaction.atomic():
        locked_slot = LayoutSlot.objects.select_for_update().get(pk=slot.pk)
        locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
        if locked_journal.status != JournalStatus.ACTIVE:
            raise ValidationError("目标子期刊未启用。")
        if locked_slot.scope != LayoutSlot.Scope.JOURNAL or not locked_slot.is_active:
            raise ValidationError("批量投放只能使用启用中的子期刊版位。")
        for article in selected_articles:
            if not can_manage_placement_target(
                actor,
                article,
                ArticlePlacement.TargetType.JOURNAL,
                locked_journal,
            ):
                raise PermissionDenied("无权向该子期刊投放文章。")
        if starts_at and ends_at and ends_at <= starts_at:
            raise ValidationError("失效时间必须晚于生效时间。")

        selected_ids = [article.pk for article in selected_articles]
        eligible_ids = set(
            get_journal_placeable_articles(locked_journal)
            .filter(pk__in=selected_ids)
            .values_list("pk", flat=True)
        )
        invalid_ids = [
            article_id for article_id in selected_ids if article_id not in eligible_ids
        ]
        if invalid_ids:
            raise ValidationError(
                "所选文章包含未审核通过、停用期刊文章或不属于目标子期刊的内容。"
            )

        existing_by_article = {
            placement.article_id: placement
            for placement in ArticlePlacement.objects.select_for_update()
            .select_related("article", "slot")
            .filter(
                article_id__in=selected_ids,
                slot=locked_slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=locked_journal.slug,
                source=ArticlePlacement.Source.MANUAL,
            )
        }
        skipped = [
            placement
            for placement in existing_by_article.values()
            if placement.is_active
        ]
        reactivated = [
            placement
            for placement in existing_by_article.values()
            if not placement.is_active
        ]
        if reactivated and not has_placement_permission(actor, "change"):
            raise PermissionDenied

        new_article_ids = [
            article_id
            for article_id in selected_ids
            if article_id not in existing_by_article
        ]
        activation_count = len(new_article_ids) + len(reactivated)
        capacity_probe = ArticlePlacement(
            slot=locked_slot,
            article=selected_articles[0],
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=locked_journal.slug,
            starts_at=starts_at,
            ends_at=ends_at,
            is_active=True,
        )
        occupied = overlapping_placements(capacity_probe, lock=True).count()
        remaining = max(0, locked_slot.max_items - occupied)
        if activation_count > remaining:
            raise ValidationError(
                f"版位 {locked_slot.code} 当前仅剩 {remaining} 个可用名额，"
                f"本次需要 {activation_count} 个，请减少文章数量或调整生效区间。"
            )

        next_sort_order = (
            ArticlePlacement.objects.filter(
                slot=locked_slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=locked_journal.slug,
                source=ArticlePlacement.Source.MANUAL,
                is_active=True,
            ).aggregate(value=Max("sort_order"))["value"]
            or 0
        )
        created = []
        updated = []
        selected_by_id = {article.pk: article for article in selected_articles}
        for article_id in selected_ids:
            placement = existing_by_article.get(article_id)
            if placement and placement.is_active:
                continue
            next_sort_order += 10
            if placement is None:
                placement = ArticlePlacement(
                    article=selected_by_id[article_id],
                    slot=locked_slot,
                    target_type=ArticlePlacement.TargetType.JOURNAL,
                    target_slug=locked_journal.slug,
                    source=ArticlePlacement.Source.MANUAL,
                )
                created.append(placement)
            else:
                placement.slot = locked_slot
                updated.append(placement)
            placement.starts_at = starts_at
            placement.ends_at = ends_at
            placement.is_pinned = is_pinned
            placement.sort_order = next_sort_order
            placement.is_active = True
            validate_placement_schedule(placement, lock=True)
            placement.full_clean()
            placement.save()

        publish_job = _queue_or_request_placement_publish(
            *created, *updated, actor=actor
        )
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=locked_journal,
            message="批量投放文章到同一子期刊。",
            metadata={
                "slot": locked_slot.code,
                "target_type": ArticlePlacement.TargetType.JOURNAL,
                "target_slug": locked_journal.slug,
                "created_article_ids": [item.article_id for item in created],
                "reactivated_article_ids": [item.article_id for item in updated],
                "skipped_article_ids": [item.article_id for item in skipped],
                "starts_at": starts_at.isoformat() if starts_at else None,
                "ends_at": ends_at.isoformat() if ends_at else None,
                "is_pinned": bool(is_pinned),
                **_publish_audit_metadata(publish_job),
            },
            ip_address=ip_address,
        )

    return {
        "created": created,
        "reactivated": updated,
        "skipped": skipped,
        "pending_publish_job": publish_job,
    }


def get_placeable_articles(search=""):
    """Return approved articles that may be selected for a placement."""
    final_approval = ArticleReviewRecord.objects.filter(
        article_id=OuterRef("pk"),
        action=ArticleReviewRecord.Action.FINAL_APPROVE,
        revision_id=OuterRef("approved_version_id"),
    )
    queryset = (
        ArticlePage.objects.annotate(has_final_approval=Exists(final_approval))
        .filter(
            review_status__in=PLACEABLE_REVIEW_STATUSES,
            primary_journal__status=JournalStatus.ACTIVE,
            approved_version__isnull=False,
            has_final_approval=True,
        )
        .select_related("primary_journal")
        .prefetch_related("related_journals")
        .order_by("title", "pk")
    )
    search = (search or "").strip()
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(static_slug__icontains=search)
            | Q(authors__icontains=search)
            | Q(primary_journal__name__icontains=search)
            | Q(primary_journal__name_cn__icontains=search)
        ).distinct()
    return queryset


def get_journal_placeable_articles(journal, search=""):
    """Return approved articles belonging to one active journal."""
    journal_id = getattr(journal, "pk", journal)
    return (
        get_placeable_articles(search).filter(primary_journal_id=journal_id).distinct()
    )


def get_slot_items(
    slot_code,
    target_type=ArticlePlacement.TargetType.MAIN_SITE,
    target_slug="",
    limit=None,
    journal=None,
    at=None,
    exclude_article_ids=None,
    target_category=None,
    include_active_release=False,
):
    if journal is not None:
        target_type = ArticlePlacement.TargetType.JOURNAL
        target_slug = getattr(journal, "slug", journal)
    target_slug = normalize_target_slug(target_slug)
    target_category_id = _target_category_id(target_type, target_category)
    requested_limit = _coerce_limit(limit)
    slot = LayoutSlot.objects.filter(code=slot_code).first()
    auto_enabled = (
        slot is not None
        and slot.is_active
        and slot.fill_mode == LayoutSlot.FillMode.AUTO
    )
    display_limit = requested_limit
    if display_limit is None and auto_enabled:
        display_limit = slot.max_items

    placement_queryset = (
        ArticlePlacement.objects.available_for_static_release(at=at)
        if include_active_release
        else ArticlePlacement.objects.available(at=at)
    )
    placements = (
        placement_queryset.filter(slot__code=slot_code)
        .for_target(target_type, target_slug)
        .select_related(
            "slot",
            "article",
            "article__primary_journal",
            "override_image",
        )
        .ordered_for_display()
    )
    if target_category_id is not None:
        placements = placements.filter(target_category_id=target_category_id)

    items = _deduplicate_slot_items(placements)
    excluded_article_ids = {
        int(article_id) for article_id in (exclude_article_ids or ()) if article_id
    }
    if excluded_article_ids:
        items = SlotItemList(
            item for item in items if item.article_id not in excluded_article_ids
        )
    if display_limit is not None:
        items = SlotItemList(items[:display_limit])
    if not auto_enabled:
        return items

    remaining = max(0, (display_limit or slot.max_items) - len(items))
    if remaining:
        items.extend(
            _auto_fill_items(
                slot=slot,
                slot_code=slot_code,
                target_type=target_type,
                target_slug=target_slug,
                existing_items=items,
                limit=remaining,
                at=at,
                exclude_article_ids=exclude_article_ids,
            )
        )
    return items


def get_slot_articles(
    slot_code,
    target_type=ArticlePlacement.TargetType.MAIN_SITE,
    target_slug="",
    limit=None,
    journal=None,
    at=None,
):
    return [
        placement.article
        for placement in get_slot_items(
            slot_code=slot_code,
            target_type=target_type,
            target_slug=target_slug,
            limit=limit,
            journal=journal,
            at=at,
        )
    ]


def get_home_page_placement_context(at=None):
    used_article_ids = set()
    hero_placements = list(
        get_slot_items(
            "home_hero",
            limit=HOME_SLOT_LIMITS["home_hero"],
            at=at,
        )
    )
    used_article_ids.update(_article_ids(hero_placements))

    visual_story_placements = list(
        get_slot_items(
            "home_visual_stories",
            limit=_home_slot_limit("home_visual_stories"),
            at=at,
            exclude_article_ids=used_article_ids,
        )
    )
    used_article_ids.update(_article_ids(visual_story_placements))

    featured_placements = list(
        get_slot_items(
            "home_featured",
            limit=_home_slot_limit("home_featured"),
            at=at,
            exclude_article_ids=used_article_ids,
        )
    )
    used_article_ids.update(_article_ids(featured_placements))

    latest_ai_article_placements = list(
        get_slot_items(
            "latest_ai_article",
            limit=_home_slot_limit("latest_ai_article"),
            at=at,
            exclude_article_ids=used_article_ids,
        )
    )

    return {
        "hero_placements": hero_placements,
        "visual_story_placements": visual_story_placements,
        "featured_placements": featured_placements,
        "latest_ai_article_placements": latest_ai_article_placements,
    }


def _target_category_id(target_type, target_category):
    if target_type != ArticlePlacement.TargetType.CATEGORY:
        if target_category is not None:
            raise ValueError("target_category is only valid for category placements")
        return None
    if target_category is None:
        raise ValueError("target_category is required for category placements")
    category_id = getattr(target_category, "pk", target_category)
    if category_id in (None, ""):
        raise ValueError("target_category must be a saved category or primary key")
    return int(category_id)


def _deduplicate_slot_items(placements):
    """Return one placement per article, with manual configuration winning."""
    ordered = list(placements)
    winners = {}
    for placement in ordered:
        current = winners.get(placement.article_id)
        if current is None or (
            current.source != ArticlePlacement.Source.MANUAL
            and placement.source == ArticlePlacement.Source.MANUAL
        ):
            winners[placement.article_id] = placement
    return SlotItemList(
        placement
        for placement in ordered
        if winners[placement.article_id].pk == placement.pk
    )


def _coerce_limit(limit):
    if limit in (None, ""):
        return None
    return int(limit)


def _home_slot_limit(slot_code):
    return (
        LayoutSlot.objects.filter(code=slot_code)
        .values_list("max_items", flat=True)
        .first()
        or HOME_SLOT_LIMITS[slot_code]
    )


def _article_ids(placements):
    return {
        article_id
        for article_id in (
            placement.article_id or getattr(placement.article, "pk", None)
            for placement in placements
        )
        if article_id
    }


def _auto_fill_items(
    *,
    slot,
    slot_code,
    target_type,
    target_slug,
    existing_items,
    limit,
    at,
    exclude_article_ids,
):
    excluded = _article_ids(existing_items)
    excluded.update(int(article_id) for article_id in (exclude_article_ids or ()))
    candidates = _auto_fill_candidates(slot_code, target_type, target_slug, at=at)
    if excluded:
        candidates = candidates.exclude(pk__in=excluded)

    items = SlotItemList()
    for offset, article in enumerate(candidates[:limit], start=1):
        placement = ArticlePlacement(
            slot=slot,
            article=article,
            target_type=target_type,
            target_slug=target_slug,
            sort_order=slot.max_items + offset,
        )
        placement.is_auto_filled = True
        items.append(placement)
    return items


def _auto_fill_candidates(slot_code, target_type, target_slug, at=None):
    candidates = get_approved_articles(at=at)
    if target_type == ArticlePlacement.TargetType.JOURNAL and target_slug:
        candidates = candidates.filter(
            Q(primary_journal__slug=target_slug) | Q(related_journals__slug=target_slug)
        ).distinct()
    elif target_type == ArticlePlacement.TargetType.SECTION and target_slug:
        article_type = SECTION_ARTICLE_TYPES.get(target_slug)
        if article_type:
            candidates = candidates.filter(article_type=article_type)
    elif (
        target_type == ArticlePlacement.TargetType.MAIN_SITE
        and slot_code == "latest_ai_article"
    ):
        candidates = candidates.filter(article_type=ArticlePage.ArticleType.AI_ARTICLE)
    return candidates


# Dynamic category placement contract (placements.services.category_sync).
import sys as _sys  # noqa: E402

from . import category_services as category_sync  # noqa: E402

_sys.modules[__name__ + ".category_sync"] = category_sync
