from __future__ import annotations

import uuid
from collections import defaultdict
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_journals,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditLog

from .category_services import (
    CategoryError,
    archive_category,
    batch_change_category_status,
    change_category_status,
    create_category,
    get_category_reference_counts,
    get_category_tree,
    move_category,
    preview_category_move,
    preview_category_update,
    reorder_category,
    update_category,
)
from .models import Journal, JournalCategory, JournalCategoryStatus

CATEGORY_STATUS_LABELS = {
    JournalCategoryStatus.ACTIVE: "启用",
    JournalCategoryStatus.HIDDEN: "隐藏",
    JournalCategoryStatus.DISABLED: "停用",
    JournalCategoryStatus.ARCHIVED: "归档",
}
CATEGORY_STATUS_CHOICES = tuple(CATEGORY_STATUS_LABELS.items())
CATEGORY_FILTER_STATUS_LABELS = {
    **CATEGORY_STATUS_LABELS,
    "exception": "异常（停用/归档）",
}
CATEGORY_FILTER_STATUS_CHOICES = tuple(CATEGORY_FILTER_STATUS_LABELS.items())


class CategoryAdminForm(forms.ModelForm):
    class Meta:
        model = JournalCategory
        fields = (
            "parent",
            "name",
            "code",
            "slug",
            "description",
            "seo_title",
            "search_description",
            "cover_image",
            "show_in_navigation",
            "generate_static_page",
            "aggregate_descendants",
        )
        labels = {
            "parent": "上级栏目",
            "name": "栏目名称",
            "code": "栏目代码",
            "slug": "路径标识（slug）",
            "description": "栏目说明",
            "seo_title": "SEO 标题",
            "search_description": "SEO 描述",
            "cover_image": "封面图",
            "show_in_navigation": "显示在导航中",
            "generate_static_page": "生成静态栏目页",
            "aggregate_descendants": "统计时包含子栏目文章",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "search_description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, journal, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal = journal
        self.actor = actor
        self.fields["parent"].queryset = JournalCategory.objects.filter(
            journal=journal
        ).order_by("path_cache")
        self.fields["parent"].empty_label = "根栏目"
        self.fields["slug"].help_text = (
            "修改后会改变本栏目及子栏目的静态路径，保存前必须确认影响。"
        )
        if self.instance.pk:
            self.fields["parent"].disabled = True
            if not (
                is_super_admin(actor)
                or can_manage_journal(actor, journal, "column_navigation")
            ):
                self.fields["code"].disabled = True

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if parent and parent.journal_id != self.journal.pk:
            raise ValidationError("上级栏目必须属于当前子期刊。")
        return parent


class CategoryMoveForm(forms.Form):
    category_id = forms.ModelChoiceField(
        label="要移动的栏目", queryset=JournalCategory.objects.none()
    )
    new_parent_id = forms.ModelChoiceField(
        label="新的上级栏目",
        queryset=JournalCategory.objects.none(),
        required=False,
        empty_label="移动到根级",
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, journal, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = JournalCategory.objects.filter(journal=journal).order_by(
            "path_cache"
        )
        self.fields["category_id"].queryset = queryset
        self.fields["new_parent_id"].queryset = queryset


class CategoryStatusForm(forms.Form):
    category_id = forms.ModelChoiceField(
        label="栏目", queryset=JournalCategory.objects.none()
    )
    new_status = forms.ChoiceField(label="新状态", choices=CATEGORY_STATUS_CHOICES)
    reason = forms.CharField(
        label="操作理由", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    confirmed = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, journal, allow_archive=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category_id"].queryset = JournalCategory.objects.filter(
            journal=journal
        ).order_by("path_cache")
        if not allow_archive:
            self.fields["new_status"].choices = tuple(
                choice
                for choice in CATEGORY_STATUS_CHOICES
                if choice[0] != JournalCategoryStatus.ARCHIVED
            )

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("new_status") == JournalCategoryStatus.ARCHIVED
            and len((cleaned.get("reason") or "").strip()) < 4
        ):
            self.add_error("reason", "归档操作必须填写至少 4 个字的理由。")
        return cleaned


class BulkCreateForm(forms.Form):
    parent = forms.ModelChoiceField(
        label="同级栏目所属上级",
        queryset=JournalCategory.objects.none(),
        required=False,
        empty_label="根级栏目",
    )
    definitions = forms.CharField(
        label="栏目清单",
        widget=forms.Textarea(
            attrs={"rows": 6, "placeholder": "栏目名称|slug|栏目代码"}
        ),
        help_text="每行一个栏目，格式为：栏目名称|slug|栏目代码；最多 100 行。",
    )

    def __init__(self, *args, journal, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = JournalCategory.objects.filter(
            journal=journal
        ).order_by("path_cache")

    def clean_definitions(self):
        lines = [
            line.strip()
            for line in self.cleaned_data["definitions"].splitlines()
            if line.strip()
        ]
        if not lines or len(lines) > 100:
            raise ValidationError("批量创建必须包含 1 至 100 行。")
        parsed = []
        for row_no, line in enumerate(lines, start=1):
            parts = [part.strip() for part in line.split("|")]
            if not parts[0]:
                raise ValidationError(f"第 {row_no} 行缺少栏目名称。")
            name = parts[0]
            item_slug = parts[1] if len(parts) > 1 and parts[1] else slugify(name)
            code = (
                parts[2]
                if len(parts) > 2 and parts[2]
                else item_slug.replace("-", "_").upper()
            )
            if not item_slug or not code:
                raise ValidationError(
                    f"第 {row_no} 行无法生成 slug 或栏目代码，请显式填写。"
                )
            parsed.append({"name": name, "slug": item_slug, "code": code})
        return parsed


class BatchStatusForm(forms.Form):
    new_status = forms.ChoiceField(
        label="批量设置状态", choices=CATEGORY_STATUS_CHOICES
    )
    reason = forms.CharField(
        label="操作理由", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    confirmed = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, allow_archive=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_archive:
            self.fields["new_status"].choices = tuple(
                choice
                for choice in CATEGORY_STATUS_CHOICES
                if choice[0] != JournalCategoryStatus.ARCHIVED
            )

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("new_status") == JournalCategoryStatus.ARCHIVED
            and len((cleaned.get("reason") or "").strip()) < 4
        ):
            self.add_error("reason", "批量归档必须填写至少 4 个字的理由。")
        return cleaned


def _has(user, permission, journal=None):
    return bool(
        is_super_admin(user)
        or (
            journal is not None
            and can_manage_journal(user, journal, "column_navigation")
        )
    )


def _can_view(user):
    return (
        is_super_admin(user)
        or filter_accessible_journals(user, Journal.objects.all()).exists()
    )


def _request_id():
    return uuid.uuid4().hex


def _status_label(status):
    return CATEGORY_STATUS_LABELS.get(status, status)


def _category_rows(journal, query="", status_filter=""):
    categories = list(
        JournalCategory.objects.filter(journal=journal)
        .select_related("parent", "journal")
        .annotate(
            system_placement_count=Count(
                "placements",
                filter=Q(
                    placements__source=ArticlePlacement.Source.SYSTEM,
                    placements__placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
                ),
                distinct=True,
            ),
            redirect_count=Count("path_redirects", distinct=True),
            child_count=Count("children", distinct=True),
        )
        .order_by("parent_id", "sort_order", "name", "pk")
    )
    by_id = {item.pk: item for item in categories}
    children = defaultdict(list)
    for item in categories:
        children[item.parent_id].append(item)

    direct_articles = defaultdict(set)
    direct_sync_errors = defaultdict(set)
    assignments = ArticleCategoryAssignment.objects.filter(
        category__journal=journal,
        article__review_status__in=(ArticlePage.ReviewStatus.APPROVED, "published"),
    ).values("category_id", "article_id", "article__placement_sync_status")
    for row in assignments:
        direct_articles[row["category_id"]].add(row["article_id"])
        if (
            row["article__placement_sync_status"]
            == ArticlePage.PlacementSyncStatus.FAILED
        ):
            direct_sync_errors[row["category_id"]].add(row["article_id"])

    aggregate_cache = {}

    def aggregate_article_ids(category_id):
        if category_id in aggregate_cache:
            return aggregate_cache[category_id]
        values = set(direct_articles[category_id])
        for child in children.get(category_id, ()):
            values.update(aggregate_article_ids(child.pk))
        aggregate_cache[category_id] = values
        return values

    ordered = []

    def flatten(parent_id=None):
        for item in children.get(parent_id, ()):
            item.direct_article_count = len(direct_articles[item.pk])
            item.aggregate_article_count = len(aggregate_article_ids(item.pk))
            item.sync_error_count = len(direct_sync_errors[item.pk])
            item.status_label = _status_label(item.status)
            ordered.append(item)
            flatten(item.pk)

    flatten()
    normalized = query.strip().casefold()
    has_filter = bool(normalized or status_filter)
    matched_ids = set()
    for item in ordered:
        haystack = " ".join(
            (item.name, item.code, item.slug, item.path_cache)
        ).casefold()
        query_matches = not normalized or normalized in haystack
        status_matches = (
            not status_filter
            or (
                status_filter == "exception"
                and item.status
                in (JournalCategoryStatus.DISABLED, JournalCategoryStatus.ARCHIVED)
            )
            or item.status == status_filter
        )
        if query_matches and status_matches:
            matched_ids.add(item.pk)
    ancestor_ids = set()
    for category_id in matched_ids:
        parent_id = by_id[category_id].parent_id
        while parent_id:
            ancestor_ids.add(parent_id)
            parent_id = by_id[parent_id].parent_id
    for item in ordered:
        item.search_match = item.pk in matched_ids
        item.search_visible = (
            not has_filter or item.search_match or item.pk in ancestor_ids
        )
        item.expanded = bool(has_filter and item.pk in ancestor_ids)
    return ordered


def _redirect_to_workbench(journal, *, selected=None, query="", status_filter=""):
    params = {"journal": journal.pk}
    if selected:
        params["selected"] = selected
    if query:
        params["q"] = query
    if status_filter:
        params["status"] = status_filter
    return redirect(f"{reverse('journals_category_admin')}?{urlencode(params)}")


def _post_fields(request):
    fields = []
    for key, values in request.POST.lists():
        if key in {"csrfmiddlewaretoken", "operation", "journal", "slug_confirmed"}:
            continue
        for value in values:
            fields.append((key, value))
    return fields


def _selected_logs(category):
    if category is None:
        return AuditLog.objects.none()
    return AuditLog.objects.filter(
        Q(target_type="JournalCategory", target_id=str(category.pk))
        | Q(metadata__category_id=category.pk)
    ).select_related("actor")[:30]


@require_http_methods(["GET", "POST"])
def category_admin(request):
    if not _can_view(request.user):
        raise PermissionDenied

    journals = filter_accessible_journals(request.user, Journal.objects.all()).order_by(
        "sort_order", "name", "pk"
    )
    journal_id = request.GET.get("journal") or request.POST.get("journal")
    journal = get_object_or_404(journals, pk=journal_id) if journal_id else None
    query = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    if status_filter not in CATEGORY_FILTER_STATUS_LABELS:
        status_filter = ""
    exception_journals = Journal.objects.none()
    if journal is None and status_filter == "exception":
        exception_journals = (
            journals.annotate(
                exception_category_count=Count(
                    "categories",
                    filter=Q(
                        categories__status__in=(
                            JournalCategoryStatus.DISABLED,
                            JournalCategoryStatus.ARCHIVED,
                        )
                    ),
                )
            )
            .filter(exception_category_count__gt=0)
            .order_by("sort_order", "name", "pk")
        )
    can_add = _has(request.user, "journals.add_journalcategory", journal)
    can_change = _has(request.user, "journals.change_journalcategory", journal)
    can_move = _has(request.user, "journals.move_journalcategory", journal)
    can_status = _has(request.user, "journals.change_category_status", journal)
    can_archive = can_status and _has(
        request.user, "journals.archive_journalcategory", journal
    )
    can_audit = bool(
        is_super_admin(request.user)
        or (journal and can_manage_journal(request.user, journal, "column_navigation"))
    )
    selected_id = (
        request.GET.get("selected")
        or request.GET.get("edit")
        or request.POST.get("category_pk")
    )
    selected_category = None
    if journal and selected_id:
        selected_category = get_object_or_404(
            JournalCategory, pk=selected_id, journal=journal
        )
    elif journal and request.method == "GET":
        selected_queryset = JournalCategory.objects.filter(journal=journal)
        if query:
            selected_queryset = selected_queryset.filter(
                Q(name__icontains=query)
                | Q(code__icontains=query)
                | Q(slug__icontains=query)
                | Q(path_cache__icontains=query)
            )
        if status_filter == "exception":
            selected_queryset = selected_queryset.filter(
                status__in=(
                    JournalCategoryStatus.DISABLED,
                    JournalCategoryStatus.ARCHIVED,
                )
            )
        elif status_filter:
            selected_queryset = selected_queryset.filter(status=status_filter)
        selected_category = selected_queryset.order_by(
            "parent_id", "sort_order", "name", "pk"
        ).first()

    mode = request.GET.get("mode", "")
    edit_category = selected_category if mode not in {"add_root", "add_child"} else None
    initial = {}
    if mode == "add_child" and selected_category:
        initial["parent"] = selected_category

    edit_form = move_form = status_form = bulk_create_form = batch_status_form = None
    confirmation = None
    if journal:
        edit_form = CategoryAdminForm(
            (
                request.POST
                if request.method == "POST"
                and request.POST.get("operation") == "save_category"
                else None
            ),
            journal=journal,
            actor=request.user,
            instance=edit_category,
            initial=initial,
            prefix="category",
        )
        move_form = CategoryMoveForm(
            (
                request.POST
                if request.method == "POST"
                and request.POST.get("operation") == "move_category"
                else None
            ),
            journal=journal,
            prefix="move",
            initial={
                "category_id": selected_category,
                "expected_version": getattr(selected_category, "version", 1),
            },
        )
        status_form = CategoryStatusForm(
            (
                request.POST
                if request.method == "POST"
                and request.POST.get("operation") == "change_status"
                else None
            ),
            journal=journal,
            allow_archive=can_archive,
            prefix="status",
            initial={"category_id": selected_category},
        )
        bulk_create_form = BulkCreateForm(
            (
                request.POST
                if request.method == "POST"
                and request.POST.get("operation") == "bulk_create"
                else None
            ),
            journal=journal,
            prefix="bulk-create",
            initial={"parent": selected_category if mode == "add_child" else None},
        )
        batch_status_form = BatchStatusForm(
            (
                request.POST
                if request.method == "POST"
                and request.POST.get("operation") == "batch_status"
                else None
            ),
            allow_archive=can_archive,
            prefix="batch-status",
        )

    if request.method == "POST" and journal:
        operation = request.POST.get("operation")
        try:
            if operation == "save_category":
                permission = (
                    "journals.change_journalcategory"
                    if edit_category
                    else "journals.add_journalcategory"
                )
                if not _has(request.user, permission, journal):
                    raise PermissionDenied
                if edit_form.is_valid():
                    values = dict(edit_form.cleaned_data)
                    parent = values.pop("parent")
                    if edit_category:
                        new_slug = values.get("slug", edit_category.slug)
                        if new_slug != edit_category.slug and not request.POST.get(
                            "slug_confirmed"
                        ):
                            preview = preview_category_update(
                                category_id=edit_category.pk,
                                new_slug=new_slug,
                                actor=request.user,
                            )
                            confirmation = {
                                "kind": "slug",
                                "category": edit_category,
                                "impact": preview.impact,
                                "post_fields": _post_fields(request),
                            }
                        else:
                            result = update_category(
                                category_id=edit_category.pk,
                                changes=values,
                                actor=request.user,
                                request_id=_request_id(),
                            )
                            messages.success(
                                request, f"栏目已保存：{result.category.full_path}"
                            )
                            return _redirect_to_workbench(
                                journal,
                                selected=result.category.pk,
                                query=query,
                                status_filter=status_filter,
                            )
                    else:
                        result = create_category(
                            journal=journal,
                            parent=parent,
                            data=values,
                            actor=request.user,
                            request_id=_request_id(),
                        )
                        messages.success(
                            request, f"栏目已创建：{result.category.full_path}"
                        )
                        return _redirect_to_workbench(
                            journal,
                            selected=result.category.pk,
                            query=query,
                            status_filter=status_filter,
                        )

            elif operation == "reorder_category":
                if not _has(request.user, "journals.move_journalcategory", journal):
                    raise PermissionDenied
                category = get_object_or_404(
                    JournalCategory,
                    pk=request.POST.get("category_id"),
                    journal=journal,
                )
                result = reorder_category(
                    category_id=category.pk,
                    direction=request.POST.get("direction") or None,
                    target_id=request.POST.get("target_id") or None,
                    position=request.POST.get("position") or "before",
                    actor=request.user,
                    request_id=_request_id(),
                )
                messages.success(
                    request,
                    (
                        "栏目顺序已更新。"
                        if result.impact.get("changed")
                        else "栏目已经位于目标位置。"
                    ),
                )
                return _redirect_to_workbench(
                    journal,
                    selected=category.pk,
                    query=query,
                    status_filter=status_filter,
                )

            elif operation == "bulk_create":
                if not _has(request.user, "journals.add_journalcategory", journal):
                    raise PermissionDenied
                if bulk_create_form.is_valid():
                    parent = bulk_create_form.cleaned_data["parent"]
                    definitions = bulk_create_form.cleaned_data["definitions"]
                    current_order = (
                        JournalCategory.objects.filter(
                            journal=journal, parent=parent
                        ).aggregate(value=Max("sort_order"))["value"]
                        or 0
                    )
                    created = []
                    with transaction.atomic():
                        for index, definition in enumerate(definitions, start=1):
                            result = create_category(
                                journal=journal,
                                parent=parent,
                                data={
                                    **definition,
                                    "sort_order": current_order + index * 10,
                                },
                                actor=request.user,
                                request_id=_request_id(),
                            )
                            created.append(result.category)
                    messages.success(request, f"已批量创建 {len(created)} 个同级栏目。")
                    return _redirect_to_workbench(
                        journal,
                        selected=created[0].pk if created else None,
                        query=query,
                        status_filter=status_filter,
                    )

            elif operation == "batch_status":
                if not can_status:
                    raise PermissionDenied
                if (
                    request.POST.get("batch-status-new_status")
                    == JournalCategoryStatus.ARCHIVED
                    and not can_archive
                ):
                    raise PermissionDenied
                category_ids = request.POST.getlist("category_ids")
                if len(category_ids) == 1 and "," in category_ids[0]:
                    category_ids = [
                        value for value in category_ids[0].split(",") if value
                    ]
                ids = list(dict.fromkeys(int(value) for value in category_ids))
                if not ids or len(ids) > 100:
                    raise CategoryError(
                        "CATEGORY_BATCH_SIZE", "批量操作必须选择 1 至 100 个栏目。"
                    )
                selected_batch = list(
                    JournalCategory.objects.filter(pk__in=ids, journal=journal)
                )
                if len(selected_batch) != len(ids):
                    raise CategoryError(
                        "CATEGORY_BATCH_CROSS_JOURNAL", "批量操作不能跨子期刊。"
                    )
                if batch_status_form.is_valid():
                    new_status = batch_status_form.cleaned_data["new_status"]
                    reason = batch_status_form.cleaned_data["reason"].strip()
                    if not batch_status_form.cleaned_data["confirmed"]:
                        references = {
                            "children": 0,
                            "article_assignments": 0,
                            "static_assignments": 0,
                            "placements": 0,
                            "redirects": 0,
                        }
                        for item in selected_batch:
                            counts = get_category_reference_counts(item)
                            for key, value in counts.items():
                                references[key] += value
                        confirmation = {
                            "kind": "batch_status",
                            "categories": selected_batch,
                            "category_ids": ids,
                            "new_status": new_status,
                            "new_status_label": _status_label(new_status),
                            "reason": reason,
                            "references": references,
                        }
                    else:
                        batch_change_category_status(
                            category_ids=ids,
                            new_status=new_status,
                            actor=request.user,
                            request_id=_request_id(),
                            reason=reason,
                        )
                        messages.success(request, f"已更新 {len(ids)} 个栏目的状态。")
                        return _redirect_to_workbench(
                            journal,
                            selected=ids[0],
                            query=query,
                            status_filter=status_filter,
                        )

            elif operation == "move_category" and move_form.is_valid():
                if not _has(request.user, "journals.move_journalcategory", journal):
                    raise PermissionDenied
                category = move_form.cleaned_data["category_id"]
                parent = move_form.cleaned_data["new_parent_id"]
                preview = preview_category_move(
                    category_id=category.pk,
                    new_parent_id=getattr(parent, "pk", None),
                    actor=request.user,
                )
                if not move_form.cleaned_data["confirmed"]:
                    confirmation = {
                        "kind": "move",
                        "impact": preview.impact,
                        "category": category,
                        "parent": parent,
                    }
                else:
                    move_category(
                        category_id=category.pk,
                        new_parent_id=getattr(parent, "pk", None),
                        expected_version=move_form.cleaned_data["expected_version"],
                        actor=request.user,
                        request_id=_request_id(),
                    )
                    messages.success(request, "栏目移动已完成，路径和重定向已同步。")
                    return _redirect_to_workbench(
                        journal,
                        selected=category.pk,
                        query=query,
                        status_filter=status_filter,
                    )

            elif operation == "change_status":
                if not can_status:
                    raise PermissionDenied
                if (
                    request.POST.get("status-new_status")
                    == JournalCategoryStatus.ARCHIVED
                    and not can_archive
                ):
                    raise PermissionDenied
                if status_form.is_valid():
                    category = status_form.cleaned_data["category_id"]
                    new_status = status_form.cleaned_data["new_status"]
                    reason = status_form.cleaned_data["reason"].strip()
                    references = get_category_reference_counts(category)
                    if not status_form.cleaned_data["confirmed"]:
                        confirmation = {
                            "kind": "status",
                            "references": references,
                            "category": category,
                            "new_status": new_status,
                            "new_status_label": _status_label(new_status),
                            "reason": reason,
                        }
                    else:
                        if new_status == JournalCategoryStatus.ARCHIVED:
                            archive_category(
                                category_id=category.pk,
                                actor=request.user,
                                request_id=_request_id(),
                                reason=reason,
                            )
                        else:
                            change_category_status(
                                category_id=category.pk,
                                new_status=new_status,
                                actor=request.user,
                                request_id=_request_id(),
                                reason=reason,
                            )
                        messages.success(request, "栏目状态已更新。")
                        return _redirect_to_workbench(
                            journal,
                            selected=category.pk,
                            query=query,
                            status_filter=status_filter,
                        )
        except (CategoryError, ValidationError, ValueError) as exc:
            error_messages = getattr(exc, "messages", None) or [str(exc)]
            messages.error(request, "；".join(error_messages))

    categories = (
        _category_rows(journal, query=query, status_filter=status_filter)
        if journal
        else []
    )
    category_tree = get_category_tree(journal=journal) if journal else []
    if journal and selected_category is None and categories:
        selected_category = categories[0]
    selected_row = next(
        (
            item
            for item in categories
            if selected_category and item.pk == selected_category.pk
        ),
        selected_category,
    )
    visible_count = sum(1 for item in categories if item.search_visible)
    selected_references = (
        get_category_reference_counts(selected_row) if selected_row else {}
    )

    return render(
        request,
        "wagtailadmin/journals/categories.html",
        {
            "title": "栏目工作台",
            "journals": journals,
            "journal": journal,
            "exception_journals": exception_journals,
            "categories": categories,
            "category_tree": category_tree,
            "category_count": len(categories),
            "visible_count": visible_count,
            "query": query,
            "status_filter": status_filter,
            "status_choices": CATEGORY_FILTER_STATUS_CHOICES,
            "soft_limit_warning": len(categories)
            > JournalCategory.SOFT_LIMIT_PER_JOURNAL,
            "selected_category": selected_row,
            "selected_redirects": (
                selected_row.path_redirects.all()[:20] if selected_row else []
            ),
            "selected_references": selected_references,
            "selected_logs": _selected_logs(selected_row),
            "edit_category": edit_category,
            "edit_form": edit_form,
            "move_form": move_form,
            "status_form": status_form,
            "bulk_create_form": bulk_create_form,
            "batch_status_form": batch_status_form,
            "confirmation": confirmation,
            "mode": mode,
            "can_add": can_add,
            "can_change": can_change,
            "can_move": can_move,
            "can_status": can_status,
            "can_archive": can_archive,
            "can_audit": can_audit,
        },
    )


def category_audit(request):
    if not _can_view(request.user):
        raise PermissionDenied
    accessible_journal_ids = list(
        filter_accessible_journals(request.user, Journal.objects.all()).values_list(
            "pk", flat=True
        )
    )
    category_ids = JournalCategory.objects.filter(
        journal_id__in=accessible_journal_ids
    ).values_list("pk", flat=True)
    logs = (
        AuditLog.objects.filter(
            Q(target_type="JournalCategory")
            | Q(metadata__operation__startswith="category_")
        )
        .filter(
            Q(target_id__in=category_ids)
            | Q(metadata__journal_id__in=accessible_journal_ids)
            | Q(metadata__journal_id__isnull=True, target_id__in=category_ids)
        )
        .select_related("actor")[:200]
    )
    return render(
        request,
        "wagtailadmin/journals/category_audit.html",
        {"title": "栏目变更记录", "logs": logs},
    )
