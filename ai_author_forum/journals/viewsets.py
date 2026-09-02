from __future__ import annotations

import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_journals,
)
from ai_author_forum.site_settings.admin_views import PermissionedModuleViewSet
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.navigation import get_active_navigation_set
from ai_author_forum.site_settings.permissions import get_admin_permission_context
from ai_author_forum.site_settings.services import record_audit_event
from ai_author_forum.static_publish.models import StaticManifest
from ai_author_forum.utils.admin_i18n import admin_text
from ai_author_forum.utils.admin_ui import translate_form_to_english

from .editor_forms import JournalCreateForm
from .models import (
    Journal,
    JournalCategory,
    JournalCategoryStatus,
    JournalEditorAssignment,
    JournalStatus,
)

_ORDERING = {
    "name": ("name", "pk"),
    "-name": ("-name", "pk"),
    "az": ("az_group", "name", "pk"),
    "-az": ("-az_group", "name", "pk"),
    "article_count": ("article_count", "name", "pk"),
    "-article_count": ("-article_count", "name", "pk"),
    "updated": ("updated_at", "pk"),
    "-updated": ("-updated_at", "pk"),
}
_PER_PAGE = {25, 50, 100}
_SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _active_manifest_snapshot() -> tuple[StaticManifest | None, set[str]]:
    manifest = (
        StaticManifest.objects.filter(is_active=True)
        .only("version", "metadata", "files")
        .first()
    )
    if manifest is None:
        return None, set()
    paths = {
        str(item.get("output_path", "")).lstrip("/")
        for item in (manifest.metadata or {}).get("targets", [])
        if item.get("status") == "generated"
        and item.get("action", "upsert") != "delete"
    }
    paths.update(
        str(item.get("path", "")).lstrip("/")
        for item in (manifest.files or [])
        if item.get("path")
    )
    return manifest, paths


def _journal_static_state(journal, manifest_paths):
    static_path = (
        journal.static_site_path or f"/journals/{journal.slug}/index.html"
    ).lstrip("/")
    frontend_url = "/" + static_path
    if frontend_url.endswith("/index.html"):
        frontend_url = frontend_url[: -len("index.html")]
    return {
        "path": static_path,
        "frontend_url": frontend_url,
        "is_published": static_path in manifest_paths,
    }


def _status_choices():
    labels = {
        JournalStatus.DRAFT: "草稿",
        JournalStatus.ACTIVE: "启用",
        JournalStatus.PAUSED: "停用",
        JournalStatus.ARCHIVED: "归档",
    }
    return [(value, labels.get(value, label)) for value, label in JournalStatus.choices]


def _prepared_chief_editor_count(journal):
    """Count a chief assignment that can become effective after activation."""
    now = timezone.now()
    return (
        JournalEditorAssignment.objects.filter(
            journal=journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            is_active=True,
            user__is_active=True,
            user__account_status="active",
        )
        .filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gt=now),
        )
        .count()
    )


class JournalsViewSet(PermissionedModuleViewSet):
    name = "journals"
    menu_label = admin_text("journals")
    menu_name = "journals"
    menu_icon = "doc-full"
    menu_order = 210
    permission = "site_settings.access_journals"
    title = admin_text("journals.title")
    description = admin_text("journals.description")
    owner = "B：journals 应用；A 提供工程、菜单、权限和审计基线。"
    integration_points = ("get_active_journals()", "get_journal_context(slug)")

    def create_view(self, request):
        if (
            not self.has_access(request)
            or not request.user.groups.filter(name="超级管理员").exists()
        ):
            raise PermissionDenied

        form = JournalCreateForm(request.POST or None)
        translate_form_to_english(form)
        if request.method == "POST" and form.is_valid():
            with transaction.atomic():
                journal = form.save(commit=False)
                # A journal must have a chief editor before it can be activated.
                # Keeping new records as drafts makes that dependency explicit.
                journal.status = JournalStatus.DRAFT
                journal.full_clean()
                journal.save()
                JournalCategory.objects.create(
                    journal=journal,
                    name="主栏目",
                    code="MAIN",
                    slug="main",
                    depth=1,
                    path_cache="main",
                    description="系统生成的默认主栏目，可在栏目工作台中调整。",
                    sort_order=0,
                    status=JournalCategoryStatus.ACTIVE,
                    show_in_navigation=True,
                    generate_static_page=True,
                    created_by=request.user,
                    updated_by=request.user,
                )
                record_audit_event(
                    request=request,
                    actor=request.user,
                    action=AuditAction.CONFIGURE,
                    status=AuditStatus.SUCCESS,
                    target=journal,
                    message="创建子期刊并初始化默认栏目。",
                    metadata={
                        "operation": "create_journal_onboarding",
                        "journal_id": journal.pk,
                        "slug": journal.slug,
                        "status": journal.status,
                    },
                )
            messages.success(
                request,
                "子期刊已创建。默认导航和主栏目已准备好，请按工作台的下一步继续。",
            )
            return redirect("journals:workspace", journal_id=journal.pk)

        return render(
            request,
            "wagtailadmin/journals/create.html",
            {
                "title": "新建子期刊",
                "form": form,
                "back_url": reverse("journals:index"),
            },
        )

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied

        base = filter_accessible_journals(request.user, Journal.objects.all()).annotate(
            article_count=Count(
                "primary_articles",
                filter=Q(
                    primary_articles__review_status__in=(
                        "approved",
                        "published",
                    )
                ),
                distinct=True,
            )
        )
        totals = filter_accessible_journals(
            request.user, Journal.objects.all()
        ).aggregate(
            total=Count("pk"),
            active=Count("pk", filter=Q(status=JournalStatus.ACTIVE)),
        )
        q = request.GET.get("q", "").strip()
        status = request.GET.get("status", "").strip()
        az = request.GET.get("az", "").strip().upper()
        ordering = request.GET.get("ordering", "").strip() or "name"
        if ordering not in _ORDERING:
            ordering = "name"
        try:
            per_page = int(request.GET.get("per_page", 25))
        except (TypeError, ValueError):
            per_page = 25
        if per_page not in _PER_PAGE:
            per_page = 25

        queryset = base
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(name_cn__icontains=q) | Q(slug__icontains=q)
            )
        valid_statuses = {value for value, _label in JournalStatus.choices}
        if status in valid_statuses:
            queryset = queryset.filter(status=status)
        else:
            status = ""
        if az in set("ABCDEFGHIJKLMNOPQRSTUVWXYZ#"):
            queryset = queryset.filter(az_group=az)
        else:
            az = ""
        queryset = queryset.order_by(*_ORDERING[ordering])

        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(request.GET.get("p"))
        _manifest, manifest_paths = _active_manifest_snapshot()
        flags = get_admin_permission_context(request.user)
        can_change = any(
            can_manage_journal(
                request.user,
                journal,
                "journal_profile",
            )
            for journal in page_obj.object_list
        )
        for journal in page_obj.object_list:
            static_state = _journal_static_state(journal, manifest_paths)
            journal.is_static_published = static_state["is_published"]
            journal.frontend_url = static_state["frontend_url"]
            journal.workspace_url = reverse("journals:workspace", args=[journal.pk])
            journal.has_slug_warning = not bool(
                _SAFE_SLUG.fullmatch(journal.slug or "")
            )
            journal.has_az_warning = journal.az_group not in set(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ#"
            )
            journal.status_label_cn = dict(_status_choices()).get(
                journal.status,
                journal.get_status_display(),
            )
            if can_manage_journal(request.user, journal, "journal_profile"):
                journal.admin_edit_url = reverse("journals_profile", args=[journal.pk])

        preserved = request.GET.copy()
        preserved.pop("p", None)
        return render(
            request,
            "wagtailadmin/journals/index.html",
            {
                "title": self.title,
                "journals": page_obj.object_list,
                "page_obj": page_obj,
                "paginator": paginator,
                "journal_count": totals["total"],
                "active_count": totals["active"],
                "filtered_count": paginator.count,
                "q": q,
                "selected_status": status,
                "selected_az": az,
                "selected_ordering": ordering,
                "per_page": per_page,
                "status_choices": _status_choices(),
                "az_choices": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"],
                "query_without_page": preserved.urlencode(),
                "can_add": flags["can_add_journal"],
                "can_import": flags["can_import_journals"],
                "can_change": can_change,
                "can_view_snippets": flags["can_view_journals"] or can_change,
                "import_url": reverse("journals_import_dashboard"),
                "add_url": reverse("journals:new"),
                "snippet_list_url": reverse("wagtailsnippets_journals_journal:list"),
            },
        )

    def workspace_view(self, request, journal_id):
        if not self.has_access(request):
            raise PermissionDenied

        journal = get_object_or_404(
            filter_accessible_journals(request.user, Journal.objects.all()),
            pk=journal_id,
        )
        flags = get_admin_permission_context(request.user)
        manifest, manifest_paths = _active_manifest_snapshot()
        static_state = _journal_static_state(journal, manifest_paths)
        primary_articles = ArticlePage.objects.filter(primary_journal=journal)
        related_articles = ArticlePage.objects.filter(
            related_journals=journal
        ).distinct()
        current_placements = ArticlePlacement.objects.available().for_target(
            ArticlePlacement.TargetType.JOURNAL,
            journal.slug,
        )
        category_count = JournalCategory.objects.filter(journal=journal).count()
        approved_primary_article_count = primary_articles.filter(
            review_status__in=("approved", "published")
        ).count()
        placement_action_label = (
            "管理本刊投放" if flags["can_manage_placement"] else "查看本刊投放"
        )
        chief_editor_count = _prepared_chief_editor_count(journal)
        navigation_set = get_active_navigation_set(journal=journal)

        actions = {
            "edit": (
                reverse("journals_profile", args=[journal.pk])
                if can_manage_journal(request.user, journal, "journal_profile")
                else ""
            ),
            "editorial_team": reverse("journals_editorial_team", args=[journal.pk]),
            "articles": (
                f"{reverse('article_admin:index')}?primary_journal={journal.pk}"
                if flags["can_view_articles"] or flags["can_edit_article"]
                else ""
            ),
            "import_articles": (
                f"{reverse('article_admin:import')}?journal={journal.pk}"
                if flags.get("can_import_articles")
                else ""
            ),
            "categories": (
                f"{reverse('journals_category_admin')}?journal={journal.pk}"
                if flags["can_view_journal_categories"]
                else ""
            ),
            "placements": (
                f"{reverse('placements:index')}?target=journal:{journal.slug}"
                if flags["can_view_placements"] or flags["can_manage_placement"]
                else ""
            ),
            "static_publish": (
                f"{reverse('static_publish:center')}?journal={journal.slug}"
                if flags["can_view_static_publish"] or flags["can_publish_static"]
                else ""
            ),
            "navigation": (
                f"{reverse('managed_navigation_admin')}?mode=journal&journal={journal.pk}"
                if navigation_set and journal.status == JournalStatus.ACTIVE
                else ""
            ),
        }
        steps = [
            {
                "title": "主编辑",
                "description": "先任命且仅保留一名有效主编辑，完成后再启用子期刊。",
                "complete": chief_editor_count == 1,
                "url": actions["editorial_team"],
                "action": "管理编辑团队",
            },
            {
                "title": "导航与栏目",
                "description": "创建时已复制默认导航并生成主栏目，可按本刊内容继续调整。",
                "complete": bool(navigation_set and category_count),
                "url": actions["navigation"] or actions["categories"],
                "action": "管理本刊导航" if actions["navigation"] else "管理本刊栏目",
            },
            {
                "title": "启用子期刊",
                "description": "资料、主编辑和栏目准备好后再启用；草稿不会进入前台目录。",
                "complete": journal.status == JournalStatus.ACTIVE,
                "url": actions["edit"],
                "action": "启用子期刊",
            },
            {
                "title": "正式内容",
                "description": "文章必须完成审核；仅保存草稿或导入成功不会进入前台。",
                "complete": approved_primary_article_count > 0,
                "url": actions["articles"] or actions["import_articles"],
                "action": "查看或导入文章",
            },
            {
                "title": "前台投放",
                "description": "审核通过后还要投放到本刊版位，发布时才会生成对应展示内容。",
                "complete": current_placements.count() > 0,
                "url": actions["placements"],
                "action": placement_action_label,
            },
            {
                "title": "静态发布",
                "description": "预估本刊目标后入队；成功并切换活动 manifest 才算上线。",
                "complete": static_state["is_published"],
                "url": actions["static_publish"],
                "action": (
                    "查看本刊发布" if static_state["is_published"] else "发布本刊"
                ),
            },
        ]
        next_step = next((step for step in steps if not step["complete"]), steps[-1])
        return render(
            request,
            "wagtailadmin/journals/workspace.html",
            {
                "title": journal.name_cn or journal.name,
                "journal": journal,
                "status_label": dict(_status_choices()).get(
                    journal.status,
                    journal.get_status_display(),
                ),
                "primary_article_count": primary_articles.count(),
                "approved_primary_article_count": primary_articles.filter(
                    review_status__in=("approved", "published")
                ).count(),
                "related_article_count": related_articles.count(),
                "category_count": category_count,
                "current_placement_count": current_placements.count(),
                "static_state": static_state,
                "active_manifest_version": manifest.version if manifest else "",
                "chief_editor_count": chief_editor_count,
                "navigation_set": navigation_set,
                "onboarding_steps": steps,
                "next_step": next_step,
                "actions": actions,
                "placement_action_label": (
                    "管理本刊投放" if flags["can_manage_placement"] else "查看本刊投放"
                ),
                "back_url": reverse("journals:index"),
            },
        )

    def get_urlpatterns(self):
        return [
            path("", self.index_view, name="index"),
            path("new/", self.create_view, name="new"),
            path(
                "<int:journal_id>/workspace/",
                self.workspace_view,
                name="workspace",
            ),
        ]
