from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

from .models import (
    JournalCategory,
    JournalCategoryPathRedirect,
    JournalCategoryRedirectReason,
    JournalCategoryStatus,
)


class CategoryError(ValidationError):
    def __init__(self, code: str, message: str, *, field_name=None, context=None):
        self.code = code
        self.field_name = field_name
        self.context = context or {}
        super().__init__(message, code=code, params=self.context)


@dataclass
class CategoryResult:
    category: JournalCategory | None = None
    impact: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    audit_id: int | None = None
    redirects: list[JournalCategoryPathRedirect] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedCategory:
    category: JournalCategory
    matched_by: str
    code: str
    path: str


ASSIGNABLE_STATUSES = {
    JournalCategoryStatus.ACTIVE,
    JournalCategoryStatus.HIDDEN,
}


def get_category_navigation(*, journal):
    """Return visible active first/second-level categories as a stable tree."""
    categories = list(
        JournalCategory.objects.filter(
            journal=journal,
            status=JournalCategoryStatus.ACTIVE,
            show_in_navigation=True,
            depth__lte=2,
        ).order_by("parent_id", "sort_order", "name", "pk")
    )
    children_by_parent = {}
    for category in categories:
        children_by_parent.setdefault(category.parent_id, []).append(category)
    return tuple(
        {
            "category": category,
            "children": tuple(children_by_parent.get(category.pk, ())),
        }
        for category in children_by_parent.get(None, ())
    )


def _require_permission(actor, permission, *, journal=None):
    """Enforce the journal-scoped column/navigation responsibility.

    Django model permissions remain a technical menu hint only; business
    authorization is always derived from the active assignment.
    """
    if is_super_admin(actor):
        return
    if journal is None or not can_manage_journal(
        actor,
        journal,
        "column_navigation",
    ):
        raise PermissionDenied(permission)


def _canonical_path(category, path_cache=None):
    path_cache = path_cache if path_cache is not None else category.path_cache
    return f"/journals/{category.journal.slug}/categories/{path_cache}/"


def _calculate_position(parent, slug):
    depth = 1 if parent is None else parent.depth + 1
    path = slug if parent is None else f"{parent.path_cache}/{slug}"
    return depth, path


def _audit(*, actor, category, request_id, operation, before=None, after=None, **extra):
    record = record_audit_event(
        action=AuditAction.CONFIGURE,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=category,
        request_id=request_id or "",
        message=f"Journal category {operation}",
        metadata={
            "operation": operation,
            "journal_id": category.journal_id,
            "category_id": category.pk,
            "before": before or {},
            "after": after or {},
            **extra,
        },
    )
    return getattr(record, "pk", None)


def create_category(*, journal, parent=None, data, actor=None, request_id=""):
    _require_permission(actor, "journals.add_journalcategory", journal=journal)
    with transaction.atomic():
        JournalCategory.objects.select_for_update().filter(journal=journal).count()
        if (
            JournalCategory.objects.filter(journal=journal).count()
            >= JournalCategory.HARD_LIMIT_PER_JOURNAL
        ):
            raise CategoryError(
                "CATEGORY_LIMIT_EXCEEDED",
                "A journal cannot contain more than 100 categories.",
                context={"journal_id": journal.pk},
            )
        if parent is not None:
            parent = JournalCategory.objects.select_for_update().get(pk=parent.pk)
            if parent.journal_id != journal.pk:
                raise CategoryError(
                    "CATEGORY_CROSS_JOURNAL",
                    "Parent category belongs to another journal.",
                    field_name="parent",
                )
        slug = str(data.get("slug") or "").strip().strip("/")
        depth, path_cache = _calculate_position(parent, slug)
        if depth > JournalCategory.MAX_DEPTH:
            raise CategoryError(
                "CATEGORY_DEPTH_EXCEEDED", "Category depth cannot exceed three."
            )
        category = JournalCategory(
            journal=journal,
            parent=parent,
            depth=depth,
            path_cache=path_cache,
            created_by=actor,
            updated_by=actor,
            **{
                k: v
                for k, v in data.items()
                if k not in {"depth", "path_cache", "journal", "parent"}
            },
        )
        # Let database constraints arbitrate concurrent duplicate creates. Model
        # business validation still runs, while uniqueness/constraint validation is
        # deliberately deferred to the INSERT so every caller receives the same
        # machine-readable CategoryError under contention.
        category.full_clean(validate_unique=False, validate_constraints=False)
        try:
            # Isolate the INSERT in a savepoint. Catching IntegrityError directly
            # inside the outer transaction would leave it unusable and prevent the
            # conflict lookup in _integrity_error().
            with transaction.atomic():
                category.save()
        except IntegrityError as exc:
            raise _integrity_error(category, exc) from exc
        warnings = []
        count = JournalCategory.objects.filter(journal=journal).count()
        if count > JournalCategory.SOFT_LIMIT_PER_JOURNAL:
            warnings.append("This journal has more than 30 categories.")
        audit_id = _audit(
            actor=actor,
            category=category,
            request_id=request_id,
            operation="create",
            after=_snapshot(category),
        )
        return CategoryResult(category=category, warnings=warnings, audit_id=audit_id)


def update_category(*, category_id, changes, actor=None, request_id=""):
    with transaction.atomic():
        category = (
            JournalCategory.objects.select_for_update(of=("self",))
            .select_related("journal", "parent")
            .get(pk=category_id)
        )
        _require_permission(
            actor,
            "journals.change_journalcategory",
            journal=category.journal,
        )
        before = _snapshot(category)
        if "code" in changes and changes["code"] != category.code:
            _require_permission(
                actor,
                "journals.migrate_category_references",
                journal=category.journal,
            )
        if "parent" in changes or "parent_id" in changes:
            raise CategoryError(
                "CATEGORY_MOVE_SERVICE_REQUIRED",
                "Use move_category to change a category parent.",
                field_name="parent",
            )
        slug_changed = "slug" in changes and changes["slug"] != category.slug
        old_subtree = _subtree_snapshot(category) if slug_changed else []
        for name, value in changes.items():
            if name not in {
                "depth",
                "path_cache",
                "journal",
                "journal_id",
                "version",
                "created_by",
                "created_at",
            }:
                setattr(category, name, value)
        if slug_changed:
            category.slug = category.slug.strip().strip("/")
            category.path_cache = (
                category.slug
                if category.parent_id is None
                else f"{category.parent.path_cache}/{category.slug}"
            )
        category.updated_by = actor
        category.version += 1
        category.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic():
                category.save()
        except IntegrityError as exc:
            raise _integrity_error(category, exc) from exc
        redirects = []
        if slug_changed:
            redirects = _repath_descendants_and_redirect(
                category,
                old_subtree,
                actor=actor,
                reason=JournalCategoryRedirectReason.SLUG_CHANGE,
                request_id=request_id,
            )
        audit_id = _audit(
            actor=actor,
            category=category,
            request_id=request_id,
            operation="update",
            before=before,
            after=_snapshot(category),
            affected_output_count=len(redirects),
        )
        return CategoryResult(
            category=category,
            impact={"redirect_count": len(redirects)},
            audit_id=audit_id,
            redirects=redirects,
        )


def preview_category_move(*, category_id, new_parent_id=None, actor=None):
    category = JournalCategory.objects.select_related("journal", "parent").get(
        pk=category_id
    )
    _require_permission(
        actor,
        "journals.move_journalcategory",
        journal=category.journal,
    )
    parent = (
        JournalCategory.objects.select_related("journal").get(pk=new_parent_id)
        if new_parent_id
        else None
    )
    subtree = _subtree_snapshot(category)
    _validate_move(category, parent, subtree)
    new_depth, new_path = _calculate_position(parent, category.slug)
    depth_delta = new_depth - category.depth
    paths = []
    prefix = category.path_cache
    for node in subtree:
        suffix = node["path_cache"][len(prefix) :].lstrip("/")
        paths.append(
            {
                "category_id": node["id"],
                "old_path": _canonical_path(category, node["path_cache"]),
                "new_path": _canonical_path(
                    category, f"{new_path}/{suffix}" if suffix else new_path
                ),
                "new_depth": node["depth"] + depth_delta,
            }
        )
    article_count = _assignment_article_ids([item["id"] for item in subtree]).count()
    placement_count = _placement_queryset([item["id"] for item in subtree]).count()
    return CategoryResult(
        category=category,
        impact={
            "old_parent_id": category.parent_id,
            "new_parent_id": new_parent_id,
            "subtree_size": len(subtree),
            "affected_article_count": article_count,
            "affected_placement_count": placement_count,
            "affected_output_count": len(paths),
            "paths": paths,
            "expected_version": category.version,
        },
    )


def move_category(
    *, category_id, new_parent_id=None, actor=None, request_id="", expected_version=None
):
    with transaction.atomic():
        category = (
            JournalCategory.objects.select_for_update(of=("self",))
            .select_related("journal", "parent")
            .get(pk=category_id)
        )
        _require_permission(
            actor,
            "journals.move_journalcategory",
            journal=category.journal,
        )
        if expected_version is not None and category.version != expected_version:
            raise CategoryError(
                "CATEGORY_VERSION_CONFLICT",
                "Category changed after the move preview; refresh and retry.",
                context={"expected": expected_version, "actual": category.version},
            )
        parent = None
        if new_parent_id:
            parent = (
                JournalCategory.objects.select_for_update()
                .select_related("journal")
                .get(pk=new_parent_id)
            )
        subtree = _subtree_snapshot(category, lock=True)
        _validate_move(category, parent, subtree)
        before = _snapshot(category)
        old_parent_id = category.parent_id
        old_subtree = list(subtree)
        category.parent = parent
        category.depth, category.path_cache = _calculate_position(parent, category.slug)
        category.updated_by = actor
        category.version += 1
        category.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic():
                category.save()
        except IntegrityError as exc:
            raise _integrity_error(category, exc) from exc
        redirects = _repath_descendants_and_redirect(
            category,
            old_subtree,
            actor=actor,
            reason=JournalCategoryRedirectReason.MOVE,
            request_id=request_id,
        )
        category.refresh_from_db()
        article_count = _assignment_article_ids(
            [item["id"] for item in old_subtree]
        ).count()
        placement_count = _placement_queryset(
            [item["id"] for item in old_subtree]
        ).count()
        audit_id = _audit(
            actor=actor,
            category=category,
            request_id=request_id,
            operation="move",
            before=before,
            after=_snapshot(category),
            old_parent_id=old_parent_id,
            new_parent_id=category.parent_id,
            affected_article_count=article_count,
            affected_placement_count=placement_count,
            affected_output_count=len(redirects),
        )
        return CategoryResult(
            category=category,
            impact={
                "affected_article_count": article_count,
                "affected_placement_count": placement_count,
                "affected_output_count": len(redirects),
            },
            audit_id=audit_id,
            redirects=redirects,
        )


def change_category_status(
    *, category_id, new_status, actor=None, request_id="", reason=""
):
    with transaction.atomic():
        category = (
            JournalCategory.objects.select_for_update(of=("self",))
            .select_related("journal", "parent")
            .get(pk=category_id)
        )
        _require_permission(
            actor,
            "journals.change_category_status",
            journal=category.journal,
        )
        if new_status == JournalCategoryStatus.ARCHIVED:
            _require_permission(
                actor,
                "journals.archive_journalcategory",
                journal=category.journal,
            )
        before = _snapshot(category)
        category.status = new_status
        if new_status != JournalCategoryStatus.ACTIVE:
            category.show_in_navigation = False
        if new_status in {
            JournalCategoryStatus.DISABLED,
            JournalCategoryStatus.ARCHIVED,
        }:
            category.generate_static_page = False
        category.updated_by = actor
        category.version += 1
        category.full_clean()
        category.save()
        affected = 0
        if new_status in {
            JournalCategoryStatus.DISABLED,
            JournalCategoryStatus.ARCHIVED,
        }:
            from ai_author_forum.placements.category_services import (
                disable_category_placements,
            )

            result = disable_category_placements(
                category_id=category.pk,
                actor=actor,
                request_id=request_id,
            )
            affected = result.get("disabled", 0)
        elif new_status in ASSIGNABLE_STATUSES:
            from ai_author_forum.placements.category_services import (
                repair_category_placement_drift,
            )

            transaction.on_commit(
                lambda: repair_category_placement_drift(
                    journal_id=category.journal_id,
                    dry_run=False,
                    actor=actor,
                )
            )
        audit_id = _audit(
            actor=actor,
            category=category,
            request_id=request_id,
            operation="status_change",
            before=before,
            after=_snapshot(category),
            affected_placement_count=affected,
            reason=reason.strip(),
        )
        return CategoryResult(
            category=category,
            impact={"affected_placement_count": affected},
            audit_id=audit_id,
        )


def archive_category(
    *, category_id, migration_plan=None, actor=None, request_id="", reason=""
):
    category = JournalCategory.objects.get(pk=category_id)
    references = get_category_reference_counts(category)
    if any(references.values()) and not migration_plan:
        raise CategoryError(
            "CATEGORY_REFERENCES_EXIST",
            "Category has references; provide and execute a migration plan before archiving.",
            context=references,
        )
    return change_category_status(
        category_id=category_id,
        new_status=JournalCategoryStatus.ARCHIVED,
        actor=actor,
        request_id=request_id,
        reason=reason,
    )


def _path_matches_category(category, full_path):
    value = full_path.strip().strip("/")
    if category.path_cache == value:
        return True
    names = [part.strip() for part in value.split(">") if part.strip()]
    return (
        bool(names)
        and [node.name for node in category.get_ancestors(include_self=True)] == names
    )


def preview_category_update(*, category_id, changes, actor=None):
    """Return path/reference impact before a slug-changing update."""
    category = JournalCategory.objects.select_related("journal", "parent").get(
        pk=category_id
    )
    _require_permission(
        actor,
        "journals.change_journalcategory",
        journal=category.journal,
    )
    new_slug = str(changes.get("slug", category.slug)).strip().strip("/")
    if new_slug == category.slug:
        return CategoryResult(
            category=category,
            impact={"path_changes": [], "requires_confirmation": False},
        )
    subtree = _subtree_snapshot(category)
    old_root = category.path_cache
    new_root = (
        new_slug
        if category.parent_id is None
        else f"{category.parent.path_cache}/{new_slug}"
    )
    path_changes = []
    for item in subtree:
        suffix = item["path_cache"][len(old_root) :].lstrip("/")
        new_cache = f"{new_root}/{suffix}" if suffix else new_root
        path_changes.append(
            {
                "category_id": item["id"],
                "old_path": _canonical_path(category, item["path_cache"]),
                "new_path": _canonical_path(category, new_cache),
            }
        )
    category_ids = [item["id"] for item in subtree]
    return CategoryResult(
        category=category,
        impact={
            "requires_confirmation": True,
            "subtree_size": len(subtree),
            "affected_article_count": _assignment_article_ids(category_ids).count(),
            "affected_placement_count": _placement_queryset(category_ids).count(),
            "affected_output_count": len(path_changes),
            "existing_redirect_count": JournalCategoryPathRedirect.objects.filter(
                category_id__in=category_ids, is_active=True
            ).count(),
            "path_changes": path_changes,
        },
    )


def reorder_category(
    *,
    category_id,
    actor=None,
    direction=None,
    target_id=None,
    position="before",
    request_id="",
):
    """Reorder only within one sibling set; used by buttons and controlled drag/drop."""
    with transaction.atomic():
        category = (
            JournalCategory.objects.select_for_update()
            .select_related("journal")
            .get(pk=category_id)
        )
        _require_permission(
            actor,
            "journals.move_journalcategory",
            journal=category.journal,
        )
        siblings = list(
            JournalCategory.objects.select_for_update()
            .filter(journal_id=category.journal_id, parent_id=category.parent_id)
            .order_by("sort_order", "name", "pk")
        )
        current = next(
            index for index, item in enumerate(siblings) if item.pk == category.pk
        )
        destination = current
        if direction == "up":
            destination = max(0, current - 1)
        elif direction == "down":
            destination = min(len(siblings) - 1, current + 1)
        elif target_id is not None:
            if position not in {"before", "after"}:
                raise CategoryError(
                    "CATEGORY_REORDER_POSITION_INVALID",
                    "拖动位置必须是目标栏目的前面或后面。",
                )
            try:
                target_index = next(
                    index
                    for index, item in enumerate(siblings)
                    if item.pk == int(target_id)
                )
            except (StopIteration, TypeError, ValueError) as exc:
                raise CategoryError(
                    "CATEGORY_REORDER_TARGET_INVALID", "排序目标无效或不属于同级栏目。"
                ) from exc
            destination = target_index + (1 if position == "after" else 0)
            if current < destination:
                destination -= 1
        else:
            raise CategoryError(
                "CATEGORY_REORDER_INVALID", "必须指定上移、下移或同级拖动目标。"
            )
        before = [item.pk for item in siblings]
        moved = siblings.pop(current)
        siblings.insert(destination, moved)
        if [item.pk for item in siblings] == before:
            return CategoryResult(category=category, impact={"changed": False})
        for index, item in enumerate(siblings, start=1):
            new_order = index * 10
            if item.sort_order != new_order:
                item.sort_order = new_order
                item.version += 1
                item.updated_by = actor
                item.save(
                    update_fields=("sort_order", "version", "updated_by", "updated_at")
                )
        category.refresh_from_db()
        audit_id = _audit(
            actor=actor,
            category=category,
            request_id=request_id,
            operation="category_reorder",
            before={"sibling_order": before},
            after={"sibling_order": [item.pk for item in siblings]},
            direction=direction or "drag",
            target_id=target_id,
            position=position,
        )
        return CategoryResult(
            category=category, impact={"changed": True}, audit_id=audit_id
        )


def batch_change_category_status(
    *, category_ids, new_status, actor=None, request_id="", reason=""
):
    ids = list(dict.fromkeys(int(value) for value in category_ids))
    if not ids or len(ids) > 100:
        raise CategoryError("CATEGORY_BATCH_SIZE", "批量操作必须选择 1 至 100 个栏目。")
    categories = list(JournalCategory.objects.filter(pk__in=ids))
    if len(categories) != len(ids):
        raise CategoryError(
            "CATEGORY_BATCH_NOT_FOUND", "部分栏目不存在，请刷新页面后重试。"
        )
    journal_ids = {item.journal_id for item in categories}
    if len(journal_ids) != 1:
        raise CategoryError("CATEGORY_BATCH_CROSS_JOURNAL", "批量操作不能跨子期刊。")
    _require_permission(
        actor,
        "journals.change_category_status",
        journal=categories[0].journal,
    )
    results = []
    with transaction.atomic():
        for category in categories:
            if new_status == JournalCategoryStatus.ARCHIVED:
                result = archive_category(
                    category_id=category.pk,
                    actor=actor,
                    request_id=request_id,
                    reason=reason,
                )
            else:
                result = change_category_status(
                    category_id=category.pk,
                    new_status=new_status,
                    actor=actor,
                    request_id=request_id,
                    reason=reason,
                )
            results.append(result)
    return results


def resolve_category(*, journal, code=None, full_path=None, allowed_statuses=None):
    if not code and not full_path:
        raise CategoryError("CATEGORY_NOT_FOUND", "Provide a category code or path.")
    queryset = JournalCategory.objects.filter(journal=journal).select_related(
        "journal", "parent"
    )
    by_code = queryset.filter(code=code).first() if code else None
    by_path = None
    if full_path:
        value = full_path.strip().strip("/")
        by_path = queryset.filter(path_cache=value).first()
        if by_path is None:
            names = [part.strip() for part in value.split(">") if part.strip()]
            candidates = queryset.filter(name=names[-1]) if names else queryset.none()
            by_path = next(
                (
                    candidate
                    for candidate in candidates
                    if _path_matches_category(candidate, full_path)
                ),
                None,
            )
    if code and by_code is None or full_path and by_path is None:
        outside = JournalCategory.objects.exclude(journal=journal).select_related(
            "journal", "parent"
        )
        code_outside = bool(code and outside.filter(code=code).exists())
        path_outside = False
        if full_path:
            value = full_path.strip().strip("/")
            path_outside = outside.filter(path_cache=value).exists()
            if not path_outside:
                names = [part.strip() for part in value.split(">") if part.strip()]
                candidates = outside.filter(name=names[-1]) if names else outside.none()
                path_outside = any(
                    _path_matches_category(candidate, full_path)
                    for candidate in candidates
                )
        if code_outside or path_outside:
            raise CategoryError(
                "CATEGORY_CROSS_JOURNAL",
                "Category code/path belongs to another journal.",
            )
        raise CategoryError("CATEGORY_NOT_FOUND", "Category code/path was not found.")
    if by_code and by_path and by_code.pk != by_path.pk:
        raise CategoryError(
            "CATEGORY_RESOLUTION_CONFLICT",
            "Category code and path resolve to different categories.",
        )
    category = by_code or by_path
    statuses = set(allowed_statuses or ASSIGNABLE_STATUSES)
    if category.status not in statuses:
        raise CategoryError(
            "CATEGORY_INACTIVE",
            "Category status does not allow this operation.",
            context={"category_id": category.pk, "status": category.status},
        )
    matched_by = "code_and_path" if code and full_path else "code" if code else "path"
    return ResolvedCategory(
        category=category,
        matched_by=matched_by,
        code=category.code,
        path=category.path_cache,
    )


def get_category_tree(*, journal, statuses=None, max_depth=3):
    queryset = JournalCategory.objects.filter(
        journal=journal, depth__lte=max_depth
    ).select_related("parent", "journal")
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    nodes = list(queryset.order_by("depth", "parent_id", "sort_order", "name", "pk"))
    by_parent = {}
    for node in nodes:
        by_parent.setdefault(node.parent_id, []).append(node)

    def build(parent_id=None):
        return [
            {"category": node, "children": build(node.pk)}
            for node in by_parent.get(parent_id, [])
        ]

    return build()


def get_category_reference_counts(category):
    from ai_author_forum.articles.models import ArticleCategoryAssignment

    from .models import StaticArticleCategoryAssignment

    return {
        "children": category.children.count(),
        "article_assignments": ArticleCategoryAssignment.objects.filter(
            category=category
        ).count(),
        "static_assignments": StaticArticleCategoryAssignment.objects.filter(
            category=category
        ).count(),
        "placements": category.placements.count(),
        "redirects": category.path_redirects.count(),
    }


def _validate_move(category, parent, subtree):
    ids = {item["id"] for item in subtree}
    if parent and parent.journal_id != category.journal_id:
        raise CategoryError(
            "CATEGORY_CROSS_JOURNAL", "New parent belongs to another journal."
        )
    if parent and parent.pk in ids:
        raise CategoryError(
            "CATEGORY_CYCLE_DETECTED",
            "Category cannot move below itself or a descendant.",
        )
    new_depth = 1 if parent is None else parent.depth + 1
    max_relative = max(item["depth"] - category.depth for item in subtree)
    if new_depth + max_relative > JournalCategory.MAX_DEPTH:
        raise CategoryError(
            "CATEGORY_DEPTH_EXCEEDED", "Move would create a fourth-level category."
        )


def _subtree_snapshot(category, lock=False):
    ids = category.get_descendant_ids(include_self=True)
    queryset = JournalCategory.objects.filter(pk__in=ids).order_by("depth", "pk")
    if lock:
        queryset = queryset.select_for_update()
    return list(queryset.values("id", "depth", "path_cache", "parent_id"))


def _repath_descendants_and_redirect(
    category, old_subtree, *, actor, reason, request_id
):
    old_by_id = {item["id"]: item for item in old_subtree}
    root_old = old_by_id[category.pk]["path_cache"]
    root_new = category.path_cache
    depth_delta = category.depth - old_by_id[category.pk]["depth"]
    descendants = list(
        JournalCategory.objects.select_for_update()
        .filter(pk__in=old_by_id)
        .select_related("journal")
    )
    redirects = []
    for node in sorted(descendants, key=lambda item: item.depth):
        old = old_by_id[node.pk]
        if node.pk != category.pk:
            suffix = old["path_cache"][len(root_old) :].lstrip("/")
            node.path_cache = f"{root_new}/{suffix}" if suffix else root_new
            node.depth = old["depth"] + depth_delta
            node.version += 1
            node.updated_by = actor
            node.full_clean()
            node.save(
                update_fields=[
                    "path_cache",
                    "depth",
                    "version",
                    "updated_by",
                    "updated_at",
                ]
            )
        old_path = _canonical_path(node, old["path_cache"])
        new_path = _canonical_path(node)
        if old_path != new_path:
            JournalCategoryPathRedirect.objects.filter(
                journal=node.journal, old_path=old_path, is_active=True
            ).update(is_active=False)
            JournalCategoryPathRedirect.objects.filter(
                journal=node.journal, new_path=old_path, is_active=True
            ).update(new_path=new_path)
            redirect = JournalCategoryPathRedirect(
                category=node,
                journal=node.journal,
                old_path=old_path,
                new_path=new_path,
                reason=reason,
                created_by=actor,
                metadata={"request_id": request_id, "old_parent_id": old["parent_id"]},
            )
            redirect.full_clean()
            redirect.save()
            redirects.append(redirect)
    return redirects


def _snapshot(category):
    return {
        "journal_id": category.journal_id,
        "parent_id": category.parent_id,
        "name": category.name,
        "code": category.code,
        "slug": category.slug,
        "depth": category.depth,
        "path_cache": category.path_cache,
        "status": category.status,
        "show_in_navigation": category.show_in_navigation,
        "generate_static_page": category.generate_static_page,
        "aggregate_descendants": category.aggregate_descendants,
        "version": category.version,
    }


def _integrity_error(category, exc):
    if (
        JournalCategory.objects.filter(journal=category.journal, code=category.code)
        .exclude(pk=category.pk)
        .exists()
    ):
        return CategoryError(
            "CATEGORY_DUPLICATE_CODE", "Category code already exists in this journal."
        )
    siblings = JournalCategory.objects.filter(
        journal=category.journal, parent=category.parent, slug=category.slug
    ).exclude(pk=category.pk)
    if siblings.exists():
        return CategoryError(
            "CATEGORY_DUPLICATE_SLUG", "Category slug already exists under this parent."
        )
    return CategoryError("CATEGORY_CONSTRAINT_VIOLATION", str(exc))


def _assignment_article_ids(category_ids):
    from ai_author_forum.articles.models import ArticleCategoryAssignment

    return (
        ArticleCategoryAssignment.objects.filter(category_id__in=category_ids)
        .values("article_id")
        .distinct()
    )


def _placement_queryset(category_ids):
    from ai_author_forum.placements.models import ArticlePlacement

    return ArticlePlacement.objects.filter(target_category_id__in=category_ids)
