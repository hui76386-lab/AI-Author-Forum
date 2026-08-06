from __future__ import annotations

import json
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_journals,
    is_super_admin,
)
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.static_publish.tasks import run_static_publish

from .models import (
    AuditAction,
    AuditLog,
    AuditStatus,
    ContentColumnConfig,
    NavigationArea,
    NavigationEntryStatus,
    NavigationGroup,
    NavigationItem,
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
    NavigationTargetType,
)
from .navigation import (
    _group_snapshot,
    _item_snapshot,
    archive_navigation_item,
    assert_can_hard_delete_navigation_item,
    copy_template_to_journal,
    duplicate_navigation_item,
    ensure_default_journal_navigation_template,
    ensure_main_navigation_set,
    hard_delete_navigation_item,
    navigation_audit_metadata,
    navigation_change_impact,
    navigation_item_reference_counts,
    record_navigation_group_change,
    record_navigation_item_change,
    reorder_navigation_tree,
    restore_navigation_item,
    set_navigation_group_visibility,
    set_navigation_item_visibility,
)


class NavigationGroupForm(forms.ModelForm):
    confirm_soft_limit = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = NavigationGroup
        fields = ("label", "code", "is_visible", "status")


class NavigationItemForm(forms.ModelForm):
    confirm_soft_limit = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = NavigationItem
        fields = (
            "label",
            "code",
            "target_type",
            "category",
            "page",
            "internal_path",
            "external_url",
            "open_in_new_tab",
            "is_visible",
            "status",
            "allow_direct_access",
        )

    def __init__(self, *args, navigation_set, **kwargs):
        super().__init__(*args, **kwargs)
        self.navigation_set = navigation_set
        categories = JournalCategory.objects.none()
        if navigation_set.journal_id:
            categories = JournalCategory.objects.filter(
                journal_id=navigation_set.journal_id
            ).order_by("path_cache")
        else:
            categories = (
                JournalCategory.objects.filter(journal__status="active")
                .select_related("journal")
                .order_by("journal__name", "path_cache")
            )
        self.fields["category"].queryset = categories


class ContentColumnConfigForm(forms.ModelForm):
    class Meta:
        model = ContentColumnConfig
        fields = (
            "intro",
            "cover_image",
            "category",
            "template_variant",
            "default_sort",
            "minimum_publish_items",
            "empty_behavior",
            "show_open_access_badge",
            "show_authors",
            "show_abstract",
            "enable_type_filter",
            "enable_year_filter",
            "page_size",
            "seo_title",
            "seo_description",
            "empty_message",
        )

    def __init__(self, *args, navigation_set, **kwargs):
        super().__init__(*args, **kwargs)
        if navigation_set.journal_id:
            self.fields["category"].queryset = JournalCategory.objects.filter(
                journal_id=navigation_set.journal_id
            ).order_by("path_cache")
        else:
            self.fields["category"].queryset = (
                JournalCategory.objects.filter(journal__status="active")
                .select_related("journal")
                .order_by("journal__name", "path_cache")
            )


MODE_PERMISSIONS = {
    "main": {
        "view": "site_settings.view_main_navigation",
        "manage": "site_settings.manage_main_navigation",
    },
    "journal": {
        "view": "site_settings.view_journal_navigation",
        "manage": "site_settings.manage_journal_navigation",
    },
    "template": {
        "view": "site_settings.view_navigation_template",
        "manage": "site_settings.manage_navigation_template",
    },
}


def _has(user, permission, journal=None):
    if is_super_admin(user):
        return True
    return bool(
        journal is not None and can_manage_journal(user, journal, "column_navigation")
    )


def _mode(request):
    value = request.POST.get("mode") or request.GET.get("mode") or "main"
    return value if value in MODE_PERMISSIONS else "main"


def _can_view(user, mode, journal=None):
    if is_super_admin(user):
        return True
    return (
        mode == "journal"
        and journal is not None
        and bool(can_manage_journal(user, journal, "column_navigation"))
    )


def _can_manage(user, mode, journal=None):
    if is_super_admin(user):
        return True
    return (
        mode == "journal"
        and journal is not None
        and bool(can_manage_journal(user, journal, "column_navigation"))
    )


def _navigation_set_for_request(request, mode, *, journals):
    if mode == "main":
        return ensure_main_navigation_set(actor=request.user)
    if mode == "template":
        return ensure_default_journal_navigation_template(actor=request.user)
    journal_id = request.POST.get("journal") or request.GET.get("journal")
    journal = journals.filter(pk=journal_id).first() if journal_id else journals.first()
    if journal is None:
        return None
    nav_set = (
        NavigationSet.objects.filter(
            journal=journal,
            scope=NavigationScope.JOURNAL,
            status=NavigationSetStatus.ACTIVE,
            is_template=False,
        )
        .select_related("journal", "site")
        .first()
    )
    if nav_set is None:
        raise ValidationError(
            f"Journal '{journal}' has no active navigation configuration."
        )
    return nav_set


def _redirect_url(mode, nav_set=None, **params):
    query = {"mode": mode}
    if nav_set and nav_set.journal_id:
        query["journal"] = nav_set.journal_id
    query.update(
        {key: value for key, value in params.items() if value not in (None, "")}
    )
    return f"{reverse('managed_navigation_admin')}?{urlencode(query)}"


def _validation_message(exc):
    if hasattr(exc, "message_dict"):
        return "; ".join(
            f"{field}: {', '.join(str(value) for value in values)}"
            for field, values in exc.message_dict.items()
        )
    if hasattr(exc, "messages"):
        return "; ".join(exc.messages)
    return str(exc)


def _duplicate_soft_limit(group):
    return (
        group.items.exclude(status=NavigationEntryStatus.ARCHIVED).count()
        >= NavigationGroup.MAX_ITEMS
    )


def _handle_post(request, mode, nav_set):
    payload = {}
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except (TypeError, ValueError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    operation = request.POST.get("operation", "") or payload.get("operation", "")
    journal = getattr(nav_set, "journal", None)
    can_manage = _can_manage(request.user, mode, journal)
    if operation == "reorder_tree":
        if not can_manage or not _has(
            request.user, "site_settings.move_navigationitem", journal
        ):
            raise PermissionDenied
        try:
            reorder_navigation_tree(
                nav_set,
                ordered_group_ids=payload.get("groups", []),
                items_by_group=payload.get("items", {}),
                expected_version=payload.get("expected_version"),
                actor=request.user,
            )
        except (ValueError, ValidationError) as exc:
            return JsonResponse(
                {"ok": False, "error": _validation_message(exc)}, status=409
            )
        return JsonResponse({"ok": True, "version": nav_set.version})

    if operation == "save_group":
        if not can_manage:
            raise PermissionDenied
        group_id = request.POST.get("group_id")
        group = (
            get_object_or_404(nav_set.groups, pk=group_id)
            if group_id
            else NavigationGroup(navigation_set=nav_set)
        )
        before = _group_snapshot(group) if group.pk else {}
        form = NavigationGroupForm(request.POST, instance=group)
        over_limit = (
            not group.pk
            and nav_set.groups.exclude(status=NavigationEntryStatus.ARCHIVED).count()
            >= NavigationSet.MAX_GROUPS
        )
        if (
            form.is_valid()
            and over_limit
            and not form.cleaned_data["confirm_soft_limit"]
        ):
            request._navigation_confirmation = {
                "kind": "group_soft_limit",
                "form": form,
                "message": "当前配置已达到 8 个分组的建议上限。请在前台预览确认后继续保存。",
            }
            return None
        if form.is_valid():
            group = form.save(commit=False)
            if not group.pk:
                group.sort_order = (nav_set.groups.count() or 0) + 1
            group.save()
            record_navigation_group_change(
                group, actor=request.user, before=before, created=not bool(group_id)
            )
            messages.success(request, "导航分组已保存并写入审计日志。")
            return redirect(_redirect_url(mode, nav_set))
        request._navigation_group_form = form
        return None

    if operation == "save_item":
        if not can_manage:
            raise PermissionDenied
        item_id = request.POST.get("item_id")
        group = get_object_or_404(nav_set.groups, pk=request.POST.get("group_id"))
        item = (
            get_object_or_404(
                NavigationItem.objects.select_related("group__navigation_set"),
                pk=item_id,
                group__navigation_set=nav_set,
            )
            if item_id
            else NavigationItem(
                site=nav_set.site,
                area=(
                    NavigationArea.JOURNALS
                    if nav_set.scope == NavigationScope.JOURNAL
                    else NavigationArea.HOME
                ),
                group=group,
                is_core=not nav_set.is_template,
            )
        )
        if item.pk and item.group_id != group.pk:
            item.group = group
        before = _item_snapshot(item) if item.pk else {}
        form = NavigationItemForm(request.POST, instance=item, navigation_set=nav_set)
        over_limit = not item.pk and _duplicate_soft_limit(group)
        if (
            form.is_valid()
            and over_limit
            and not form.cleaned_data["confirm_soft_limit"]
        ):
            request._navigation_confirmation = {
                "kind": "item_soft_limit",
                "form": form,
                "group": group,
                "message": "当前分组已达到 20 个栏目的建议上限。请在前台预览确认后继续保存。",
            }
            return None
        if form.is_valid():
            item = form.save(commit=False)
            item.group = group
            item.site = nav_set.site
            item.area = (
                NavigationArea.JOURNALS
                if nav_set.scope == NavigationScope.JOURNAL
                else NavigationArea.HOME
            )
            item.slug = item.code
            item.updated_by = request.user
            if not item.pk:
                item.sort_order = group.items.count() + 1
            item.save()
            if item.target_type == NavigationTargetType.CONTENT_COLUMN:
                ContentColumnConfig.objects.get_or_create(navigation_item=item)
            record_navigation_item_change(
                item, actor=request.user, before=before, created=not bool(item_id)
            )
            messages.success(request, "栏目入口已保存并写入审计日志。")
            return redirect(_redirect_url(mode, nav_set, edit_item=item.pk))
        request._navigation_item_form = form
        request._navigation_item_group = group
        return None

    if operation == "save_content_config":
        if not (
            can_manage
            or _has(
                request.user,
                "site_settings.change_contentcolumnconfig",
                journal,
            )
        ):
            raise PermissionDenied
        item = get_object_or_404(
            NavigationItem.objects.select_related("group__navigation_set"),
            pk=request.POST.get("item_id"),
            group__navigation_set=nav_set,
            target_type=NavigationTargetType.CONTENT_COLUMN,
        )
        config, _ = ContentColumnConfig.objects.get_or_create(navigation_item=item)
        before = {
            "category_id": config.category_id,
            "cover_image_id": config.cover_image_id,
            "page_size": config.page_size,
            "enable_type_filter": config.enable_type_filter,
            "enable_year_filter": config.enable_year_filter,
            "seo_title": config.seo_title,
            "seo_description": config.seo_description,
            "empty_message": config.empty_message,
        }
        form = ContentColumnConfigForm(
            request.POST, request.FILES, instance=config, navigation_set=nav_set
        )
        if form.is_valid():
            config = form.save()
            item.category = config.category
            item.updated_by = request.user
            item.save(update_fields=["category", "updated_by", "updated_at"])
            nav_set.refresh_from_db(fields=["version"])
            AuditLog.record(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.SUCCESS,
                actor=request.user,
                target=config,
                message="Updated controlled content column configuration.",
                metadata=navigation_audit_metadata(
                    nav_set,
                    before=before,
                    after={
                        "category_id": config.category_id,
                        "cover_image_id": config.cover_image_id,
                        "page_size": config.page_size,
                        "enable_type_filter": config.enable_type_filter,
                        "enable_year_filter": config.enable_year_filter,
                        "seo_title": config.seo_title,
                        "seo_description": config.seo_description,
                        "empty_message": config.empty_message,
                    },
                ),
            )
            messages.success(request, "内容栏目配置已保存。")
            return redirect(_redirect_url(mode, nav_set, edit_config=item.pk))
        request._content_config_form = form
        request._content_config_item = item
        return None

    item_operations = {
        "duplicate_item",
        "hide_item",
        "enable_item",
        "archive_item",
        "restore_item",
        "hard_delete_item",
    }
    if operation in item_operations:
        if not can_manage:
            raise PermissionDenied
        item = get_object_or_404(
            NavigationItem.objects.select_related("group__navigation_set", "site"),
            pk=request.POST.get("item_id"),
            group__navigation_set=nav_set,
        )
        try:
            if operation == "duplicate_item":
                if _duplicate_soft_limit(item.group) and not request.POST.get(
                    "confirmed"
                ):
                    request._navigation_confirmation = {
                        "kind": "duplicate_soft_limit",
                        "item": item,
                        "message": "复制后该分组将超过 20 个栏目的建议上限，请确认后继续。",
                    }
                    return None
                duplicate_navigation_item(item, actor=request.user)
            elif operation == "hide_item":
                set_navigation_item_visibility(item, visible=False, actor=request.user)
            elif operation == "enable_item":
                set_navigation_item_visibility(item, visible=True, actor=request.user)
            elif operation == "archive_item":
                archive_navigation_item(item, actor=request.user)
            elif operation == "restore_item":
                restore_navigation_item(item, actor=request.user)
            else:
                hard_delete_navigation_item(item, actor=request.user)
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "栏目入口操作已完成并写入审计日志。")
        return redirect(_redirect_url(mode, nav_set))

    if operation in {"hide_group", "enable_group"}:
        if not can_manage:
            raise PermissionDenied
        group = get_object_or_404(nav_set.groups, pk=request.POST.get("group_id"))
        set_navigation_group_visibility(
            group, visible=operation == "enable_group", actor=request.user
        )
        messages.success(request, "导航分组状态已更新。")
        return redirect(_redirect_url(mode, nav_set))

    if operation in {"template_preview", "template_copy"}:
        if mode != "journal" or nav_set is None:
            raise ValidationError(
                "Template copy is only available for a selected journal."
            )
        if not (is_super_admin(request.user)):
            raise PermissionDenied
        template = ensure_default_journal_navigation_template(actor=request.user)
        impact = navigation_change_impact(nav_set)
        preview = {
            "kind": "template_copy",
            "template": template,
            "current_groups": nav_set.groups.count(),
            "current_items": NavigationItem.objects.filter(
                group__navigation_set=nav_set
            ).count(),
            "template_groups": template.groups.count(),
            "template_items": NavigationItem.objects.filter(
                group__navigation_set=template
            ).count(),
            "affected_page_count": len(impact.paths),
            "paths": impact.paths,
        }
        if operation == "template_preview" or not request.POST.get("confirmed"):
            request._navigation_confirmation = preview
            return None
        new_set = copy_template_to_journal(
            template=template,
            journal=nav_set.journal,
            actor=request.user,
            overwrite=True,
        )
        messages.success(request, "已从默认模板覆盖复制，并归档旧配置。")
        return redirect(_redirect_url(mode, new_set))

    if operation == "publish_changes":
        if not (is_super_admin(request.user)):
            raise PermissionDenied
        impact = navigation_change_impact(nav_set)
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.SELECTIVE,
            requested_paths=list(impact.paths),
            triggered_by=request.user,
        )
        AuditLog.record(
            action=AuditAction.PUBLISH,
            status=AuditStatus.STARTED,
            actor=request.user,
            target=nav_set,
            message="Queued static rebuild for navigation changes.",
            metadata=navigation_audit_metadata(
                nav_set,
                before={},
                after={"requested_paths": list(impact.paths)},
                affected_page_count=len(impact.paths),
                publish_job_id=job.pk,
            ),
        )
        try:
            task = run_static_publish.delay(job.pk)
        except Exception as exc:
            job.status = StaticPublishJob.Status.FAILED
            job.error = str(exc)
            job.save(update_fields=["status", "error"])
            AuditLog.record(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                actor=request.user,
                target=nav_set,
                message="Could not queue static rebuild for navigation changes.",
                metadata=navigation_audit_metadata(
                    nav_set,
                    before={"requested_paths": list(impact.paths)},
                    after={"job_status": StaticPublishJob.Status.FAILED},
                    affected_page_count=len(impact.paths),
                    publish_job_id=job.pk,
                    failure_reason=str(exc),
                ),
            )
            messages.error(request, f"静态发布任务入队失败：{exc}")
        else:
            messages.success(
                request,
                f"静态发布任务 #{job.pk} 已入队，共 {len(impact.paths)} 个受影响页面（任务 {task.id}）。",
            )
        return redirect(_redirect_url(mode, nav_set))

    raise ValidationError("Unsupported navigation administration operation.")


def _form_context(request, nav_set, *, mode, can_manage):
    groups = list(
        nav_set.groups.prefetch_related(
            "items", "items__content_column_config"
        ).order_by("sort_order", "pk")
    )
    edit_group = None
    edit_item = None
    edit_config_item = getattr(request, "_content_config_item", None)
    group_form = getattr(request, "_navigation_group_form", None)
    item_form = getattr(request, "_navigation_item_form", None)
    config_form = getattr(request, "_content_config_form", None)

    edit_group_id = request.GET.get("edit_group")
    if edit_group_id and can_manage:
        edit_group = nav_set.groups.filter(pk=edit_group_id).first()
    if group_form is None:
        group_form = NavigationGroupForm(instance=edit_group)

    edit_item_id = request.GET.get("edit_item")
    if edit_item_id:
        edit_item = (
            NavigationItem.objects.filter(
                pk=edit_item_id, group__navigation_set=nav_set
            )
            .select_related("group", "page", "category")
            .first()
        )
    if item_form is None:
        requested_group = nav_set.groups.filter(
            pk=request.GET.get("add_to_group")
        ).first()
        initial_group = (
            getattr(request, "_navigation_item_group", None)
            or (edit_item.group if edit_item else None)
            or requested_group
            or (groups[0] if groups else None)
        )
        instance = edit_item
        if instance is None and initial_group is not None:
            instance = NavigationItem(
                site=nav_set.site,
                area=(
                    NavigationArea.JOURNALS
                    if nav_set.scope == NavigationScope.JOURNAL
                    else NavigationArea.HOME
                ),
                group=initial_group,
            )
        item_form = NavigationItemForm(instance=instance, navigation_set=nav_set)
    else:
        initial_group = getattr(request, "_navigation_item_group", None)

    edit_config_id = request.GET.get("edit_config")
    if edit_config_id and edit_config_item is None:
        edit_config_item = (
            NavigationItem.objects.filter(
                pk=edit_config_id,
                group__navigation_set=nav_set,
                target_type=NavigationTargetType.CONTENT_COLUMN,
            )
            .select_related("group")
            .first()
        )
    if config_form is None and edit_config_item:
        config, _ = ContentColumnConfig.objects.get_or_create(
            navigation_item=edit_config_item
        )
        config_form = ContentColumnConfigForm(instance=config, navigation_set=nav_set)

    reference_item = None
    references = None
    reference_id = request.GET.get("references")
    if reference_id:
        reference_item = (
            NavigationItem.objects.filter(
                pk=reference_id, group__navigation_set=nav_set
            )
            .select_related("group__navigation_set", "category")
            .first()
        )
        if reference_item:
            references = navigation_item_reference_counts(reference_item)
            try:
                assert_can_hard_delete_navigation_item(
                    reference_item, user=request.user
                )
            except (PermissionDenied, ValidationError):
                references["can_hard_delete"] = False
            else:
                references["can_hard_delete"] = True

    return {
        "groups": groups,
        "group_form": group_form,
        "edit_group": edit_group,
        "item_form": item_form,
        "item_form_group": initial_group,
        "edit_item": edit_item,
        "config_form": config_form,
        "edit_config_item": edit_config_item,
        "reference_item": reference_item,
        "references": references,
    }


@require_http_methods(["GET", "POST"])
def managed_navigation_admin(request):
    mode = _mode(request)
    journals = filter_accessible_journals(
        request.user, Journal.objects.filter(status="active")
    ).order_by("name", "pk")
    selected_journal = None
    if mode == "journal":
        journal_id = request.POST.get("journal") or request.GET.get("journal")
        selected_journal = (
            journals.filter(pk=journal_id).first() if journal_id else journals.first()
        )
    if not _can_view(request.user, mode, selected_journal):
        raise PermissionDenied
    try:
        nav_set = _navigation_set_for_request(request, mode, journals=journals)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        nav_set = None
    response = None
    if request.method == "POST" and nav_set is not None:
        try:
            response = _handle_post(request, mode, nav_set)
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        if response is not None:
            return response
    navigation_journal = getattr(nav_set, "journal", None)
    can_manage = nav_set is not None and _can_manage(
        request.user, mode, navigation_journal
    )
    context = {
        "mode": mode,
        "mode_label": {
            "main": "主站栏目",
            "journal": "子期刊栏目",
            "template": "默认子期刊模板",
        }[mode],
        "journals": journals,
        "navigation_set": nav_set,
        "can_manage": can_manage,
        "can_edit_content": can_manage,
        "can_reorder": can_manage
        and _has(
            request.user,
            "site_settings.move_navigationitem",
            navigation_journal,
        ),
        "can_publish": is_super_admin(request.user),
        "can_copy_template": (
            mode == "journal" and can_manage and is_super_admin(request.user)
        ),
        "confirmation": getattr(request, "_navigation_confirmation", None),
        "target_types": NavigationTargetType.choices,
    }
    if nav_set is not None:
        context.update(
            _form_context(request, nav_set, mode=mode, can_manage=can_manage)
        )
        context["impact"] = navigation_change_impact(nav_set)
        preview_path = (
            f"/journals/{nav_set.journal.slug}/" if nav_set.journal_id else "/"
        )
        context["preview_url"] = f"{preview_path}?admin_navigation_preview=1"
    return render(request, "wagtailadmin/navigation/manage.html", context)
