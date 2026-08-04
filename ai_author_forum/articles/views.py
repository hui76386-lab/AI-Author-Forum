import difflib
import json
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, ProgrammingError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import TemplateView
from wagtail.models import Page

from ai_author_forum.site_settings.permissions import get_admin_permission_context
from ai_author_forum.utils.admin_ui import admin_english

from .admin_filters import (
    ORDERING_CHOICES,
    PAGE_SIZES,
    ArticleAdminFilters,
    build_article_admin_queryset,
)
from .admin_services import prepare_article_admin_row
from .bulk_services import (
    EDIT_ACTIONS,
    REVIEW_ACTIONS,
    execute_bulk_article_action,
    user_can_bulk_action,
)
from .editor_services import user_can_use_raw_html
from .models import (
    ArticlePage,
    ArticleRevisionConflict,
    user_has_article_edit_permission,
    user_has_article_placement_permission,
    user_has_article_review_permission,
)

ARTICLE_DIFF_GROUPS = (
    (
        "content",
        "内容",
        [
            ("title", "标题"),
            ("abstract", "摘要"),
            ("featured_image_id", "文章封面"),
            ("featured_image_alt", "封面替代文本"),
            ("authors", "作者"),
            ("keywords", "关键词"),
            ("body", "正文"),
        ],
    ),
    (
        "classification",
        "归属分类",
        [
            ("primary_journal_id", "主属期刊"),
            ("article_type", "文章类型"),
        ],
    ),
    (
        "responsibility",
        "责任声明",
        [
            ("ai_co_authors", "AI 合著人"),
            ("ai_contribution_statement", "AI 参与说明"),
            ("responsibility_statement", "责任说明"),
        ],
    ),
    (
        "seo",
        "SEO / 路径",
        [
            ("slug", "Wagtail slug"),
            ("static_slug", "静态路径 slug"),
            ("seo_title", "SEO 标题"),
            ("search_description", "搜索描述"),
        ],
    ),
)


def user_can_review_articles(user):
    return user_has_article_review_permission(user)


class ArticleReviewPermissionMixin:
    def dispatch(self, request, *args, **kwargs):
        if not user_can_review_articles(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ArticleListView(TemplateView):
    template_name = "wagtailadmin/articles/list.html"
    pending_only = False

    def get_template_names(self):
        if (translation.get_language() or "").lower().startswith("en"):
            return ["wagtailadmin/articles/list.en.html"]
        return super().get_template_names()

    page_title = "所有文章"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.get_filters()
        page_obj = Paginator(self.get_queryset(filters), filters.page_size).get_page(
            self.request.GET.get("p")
        )
        now = timezone.now()
        articles = []
        for article in page_obj.object_list:
            prepare_article_admin_row(
                article,
                user=self.request.user,
                request=self.request,
            )
            article.waiting_duration = (
                now - article.submitted_at if article.submitted_at else None
            )
            articles.append(article)

        selected_journal = self.get_selected_journal(filters.primary_journal)
        permission_flags = get_admin_permission_context(self.request.user)
        create_article_url = self.get_create_article_url()
        article_import_url = (
            reverse("article_admin:import")
            if permission_flags.get("can_import_articles")
            else ""
        )
        journal_workspace_url = ""
        journal_placements_url = ""
        if selected_journal is not None:
            if article_import_url:
                article_import_url += f"?journal={selected_journal.pk}"
            journal_workspace_url = reverse(
                "journals:workspace",
                args=[selected_journal.pk],
            )
            if (
                permission_flags["can_view_placements"]
                or permission_flags["can_manage_placement"]
            ):
                journal_placements_url = (
                    f"{reverse('placements:index')}"
                    f"?target=journal:{selected_journal.slug}"
                )

        context.update(
            {
                "articles": articles,
                "page_obj": page_obj,
                "page_title": (
                    admin_english(self.page_title)
                    if (translation.get_language() or "").lower().startswith("en")
                    else self.page_title
                ),
                "pending_only": self.pending_only,
                "filters": filters.as_dict(),
                "status_choices": ArticlePage.ReviewStatus.choices,
                "publication_status_choices": ArticlePage.PublicationStatus.choices,
                "article_type_choices": ArticlePage.ArticleType.choices,
                "journal_choices": self.get_journal_choices(),
                "category_choices": self.get_category_choices(),
                "ordering_choices": ORDERING_CHOICES,
                "page_sizes": PAGE_SIZES,
                "review_detail_url_name": "article_admin:review_detail",
                "paginator_url_prefix": self.get_paginator_url_prefix(),
                "reset_url": self.request.path,
                "bulk_action_url": reverse("article_admin:bulk_action"),
                "current_url": self.request.get_full_path(),
                "create_article_url": create_article_url,
                "article_import_url": article_import_url,
                "can_bulk_edit": any(
                    user_can_bulk_action(self.request.user, action)
                    for action in EDIT_ACTIONS
                ),
                "can_bulk_review": any(
                    user_can_bulk_action(self.request.user, action)
                    for action in REVIEW_ACTIONS
                ),
                "journal_context": selected_journal,
                "journal_workspace_url": journal_workspace_url,
                "journal_placements_url": journal_placements_url,
            }
        )
        return context

    def get_create_article_url(self):
        if self.pending_only or not user_has_article_edit_permission(self.request.user):
            return ""

        try:
            parent_page = Page.get_first_root_node()
        except Page.DoesNotExist:
            return ""

        if parent_page is None or not ArticlePage.can_create_at(parent_page):
            return ""
        if not parent_page.permissions_for_user(self.request.user).can_add_subpage():
            return ""

        create_url = reverse(
            "wagtailadmin_pages:add",
            args=[
                ArticlePage._meta.app_label,
                ArticlePage._meta.model_name,
                parent_page.pk,
            ],
        )
        return f"{create_url}?{urlencode({'next': self.request.get_full_path()})}"

    def get_queryset(self, filters=None):
        return build_article_admin_queryset(filters or self.get_filters())

    def get_filters(self):
        return ArticleAdminFilters.from_querydict(
            self.request.GET,
            pending_only=self.pending_only,
        )

    def get_paginator_url_prefix(self):
        query_params = self.request.GET.copy()
        query_params.pop("p", None)
        encoded_query = query_params.urlencode()
        return f"?{encoded_query}&" if encoded_query else "?"

    def get_selected_journal(self, value):
        try:
            journal_id = int(value)
        except (TypeError, ValueError):
            return None
        if journal_id <= 0:
            return None
        journal_model = ArticlePage._meta.get_field(
            "primary_journal"
        ).remote_field.model
        if isinstance(journal_model, str):
            return None
        try:
            return journal_model.objects.filter(pk=journal_id).first()
        except (OperationalError, ProgrammingError):
            return None

    def get_journal_choices(self):
        journal_model = ArticlePage._meta.get_field(
            "primary_journal"
        ).remote_field.model
        if isinstance(journal_model, str):
            return []
        try:
            return journal_model.objects.order_by("name", "pk")
        except (OperationalError, ProgrammingError):
            return []

    def get_category_choices(self):
        assignment_model = ArticlePage.category_assignments.rel.related_model
        category_model = assignment_model._meta.get_field("category").remote_field.model
        try:
            return category_model.objects.select_related("journal").order_by(
                "journal__name", "path_cache", "pk"
            )
        except (OperationalError, ProgrammingError):
            return []


class PendingArticleListView(ArticleReviewPermissionMixin, ArticleListView):
    pending_only = True
    page_title = "待审核文章"


class ArticleReviewDashboardView(ArticleReviewPermissionMixin, TemplateView):
    template_name = "wagtailadmin/articles/review_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = build_article_admin_queryset(ArticleAdminFilters())
        pending_queryset = build_article_admin_queryset(
            ArticleAdminFilters(review_status=ArticlePage.ReviewStatus.SUBMITTED)
        )
        pending_articles = list(pending_queryset[:10])
        recent_articles = list(
            base_queryset.exclude(
                review_status=ArticlePage.ReviewStatus.SUBMITTED,
            )[:10]
        )
        now = timezone.now()
        for article in [*pending_articles, *recent_articles]:
            prepare_article_admin_row(
                article,
                user=self.request.user,
                request=self.request,
            )
            article.waiting_duration = (
                now - article.submitted_at if article.submitted_at else None
            )

        context.update(
            {
                "title": "文章审核",
                "pending_articles": pending_articles,
                "recent_articles": recent_articles,
                "pending_count": ArticlePage.objects.filter(
                    review_status=ArticlePage.ReviewStatus.SUBMITTED,
                ).count(),
                "approved_count": ArticlePage.objects.filter(
                    review_status=ArticlePage.ReviewStatus.APPROVED,
                ).count(),
                "published_count": ArticlePage.objects.filter(
                    publication_status=ArticlePage.PublicationStatus.PUBLISHED,
                ).count(),
                "rejected_count": ArticlePage.objects.filter(
                    review_status=ArticlePage.ReviewStatus.REJECTED,
                ).count(),
                "pending_url": reverse("article_admin:pending"),
                "all_articles_url": reverse("article_admin:index"),
            }
        )
        return context


class BulkArticleActionView(View):
    def post(self, request):
        action = request.POST.get("action", "")
        article_ids = request.POST.getlist("article_ids[]") or request.POST.getlist(
            "article_ids"
        )
        expected = {
            key.removeprefix("expected_revision_"): value
            for key, value in request.POST.items()
            if key.startswith("expected_revision_")
        }
        category = request.POST.get("category")
        if action == "set_primary_category":
            category = request.POST.get("primary_category") or category
        elif action == "add_category":
            category = request.POST.get("additional_category") or category
        params = {
            "primary_journal": request.POST.get("primary_journal"),
            "category": category,
            "article_type": request.POST.get("article_type"),
        }
        try:
            result = execute_bulk_article_action(
                user=request.user,
                article_ids=article_ids,
                action=action,
                params=params,
                comment=request.POST.get("comment", "").strip(),
                expected_revisions=expected,
            )
        except PermissionDenied:
            return JsonResponse({"error": "您没有执行此操作的权限。"}, status=403)
        except ValidationError as exc:
            return JsonResponse({"error": "；".join(exc.messages)}, status=400)
        return JsonResponse(result.as_dict())


class ArticleEditorCapabilitiesView(View):
    def get(self, request):
        return JsonResponse(
            {
                "can_use_raw_html": user_can_use_raw_html(request.user),
                "autosave_seconds": 30,
            }
        )


class ArticleSubmitReviewView(View):
    def post(self, request, page_id):
        article = self.get_article(page_id)
        if not self.can_submit_review(article):
            raise PermissionDenied

        if article.review_status not in {
            ArticlePage.ReviewStatus.DRAFT,
            ArticlePage.ReviewStatus.REJECTED,
        }:
            messages.warning(request, "只有草稿或已驳回文章可以提交审核。")
            return redirect(self.get_next_url(article))

        expected_revision_id = (
            request.POST.get("expected_revision_id")
            or request.POST.get(f"expected_revision_{article.pk}")
            or None
        )
        comment = request.POST.get("comment", "").strip() or "提交内容审核。"
        try:
            article.submit_for_review(
                request.user,
                comment,
                expected_revision_id=expected_revision_id,
            )
        except ArticleRevisionConflict as exc:
            messages.error(request, str(exc))
        except ValidationError as exc:
            messages.error(request, format_submit_review_error(exc))
        else:
            messages.success(
                request,
                "文章已提交审核，现已进入“待审核文章”。",
            )
        return redirect(self.get_next_url(article))

    def get_article(self, page_id):
        page = get_object_or_404(
            Page.objects.select_related("latest_revision"), pk=page_id
        ).specific
        if not isinstance(page, ArticlePage):
            raise Http404
        return page

    def can_submit_review(self, article):
        return bool(
            user_has_article_edit_permission(self.request.user)
            and article.permissions_for_user(self.request.user).can_edit()
        )

    def get_next_url(self, article):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return reverse("article_admin:review_detail", args=[article.pk])


def format_submit_review_error(exc):
    code = getattr(exc, "code", "")
    if code == "ARTICLE_PRIMARY_CATEGORY_REQUIRED":
        return "提交审核前必须先分配一个主栏目。请编辑文章的“动态栏目”，并勾选主栏目后再提交。"
    if code == "ARTICLE_MULTIPLE_PRIMARY_CATEGORIES":
        return "只能分配一个主栏目，请调整后再提交审核。"
    if code == "CATEGORY_CROSS_JOURNAL":
        return "选择的栏目不属于文章的主属期刊，请调整栏目或主属期刊。"
    return "；".join(exc.messages)


class ArticleReviewDetailView(ArticleReviewPermissionMixin, TemplateView):
    template_name = "wagtailadmin/articles/review_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.article = self.get_article(kwargs["page_id"])
        self.preview_article = self.get_preview_article(self.article)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        latest_revision = self.article.get_latest_revision()
        placement_url = ""
        if (
            self.article.review_status
            in {
                ArticlePage.ReviewStatus.APPROVED,
                ArticlePage.ReviewStatus.PUBLISHED,
            }
            and user_has_article_placement_permission(self.request.user)
            and self.article.primary_journal_id
            and getattr(self.article.primary_journal, "status", "") == "active"
        ):
            placement_url = (
                f"{reverse('placements:new_single')}?"
                f"{urlencode({'article': self.article.pk, 'journal': self.article.primary_journal.slug})}"
            )
        context.update(
            {
                "article": self.article,
                "preview_article": self.preview_article,
                "page_title": "审核详情",
                "preview_url": reverse(
                    "article_admin:review_preview", args=[self.article.pk]
                ),
                "edit_url": reverse("wagtailadmin_pages:edit", args=[self.article.pk]),
                "placements_url": f"{reverse('placements:index')}?article={self.article.pk}",
                "placement_url": placement_url,
                "submit_review_url": reverse(
                    "article_admin:submit_review", args=[self.article.pk]
                ),
                "can_edit_article": self.article.permissions_for_user(
                    self.request.user
                ).can_edit(),
                "latest_revision": latest_revision,
                "expected_revision_id": latest_revision.pk if latest_revision else "",
                "approved_version": self.article.approved_version,
                "rejected_version": self.article.rejected_version,
                "review_records": self.article.review_records.select_related(
                    "reviewer", "revision"
                ).order_by("created_at", "id"),
                "revision_diff": get_revision_diff(self.article),
                "can_execute_review": self.can_execute_review(),
                "can_submit_review": self.can_submit_review(),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        comment = request.POST.get("comment", "").strip()
        expected_revision_id = request.POST.get("expected_revision_id", "")

        if action not in {"approve", "reject"}:
            messages.error(request, "未知审核操作。")
            return redirect(self.get_success_url())
        if action == "reject" and not comment:
            messages.error(request, "驳回意见必填。")
            return redirect(self.get_success_url())
        if not self.can_execute_review(action):
            raise PermissionDenied

        try:
            self.perform_review_action(action, comment, expected_revision_id)
        except ArticleRevisionConflict as exc:
            messages.error(request, str(exc))
            return redirect(self.get_success_url())
        except ValidationError as exc:
            messages.error(request, "；".join(exc.messages))
            return redirect(self.get_success_url())

        if action == "approve":
            messages.success(
                request,
                "文章已审核通过。下一步请进入投放管理；当前状态不代表已发布。",
            )
        else:
            messages.success(request, "文章已驳回。")
        return redirect(self.get_success_url())

    def get_article(self, page_id):
        page = get_object_or_404(
            Page.objects.select_related("latest_revision"), pk=page_id
        ).specific
        if not isinstance(page, ArticlePage):
            raise Http404
        return page

    def get_preview_article(self, article):
        latest_revision = article.get_latest_revision()
        return latest_revision.as_object() if latest_revision else article

    def can_submit_review(self):
        return bool(
            self.article.review_status
            in {ArticlePage.ReviewStatus.DRAFT, ArticlePage.ReviewStatus.REJECTED}
            and user_has_article_edit_permission(self.request.user)
            and self.article.permissions_for_user(self.request.user).can_edit()
        )

    def can_execute_review(self, action=None):
        if not user_can_review_articles(self.request.user):
            return False
        task = self.article.current_workflow_task
        task_state = self.article.current_workflow_task_state
        if not task or not task_state:
            return self.article.review_status == ArticlePage.ReviewStatus.SUBMITTED
        actions = {
            item[0] for item in task.get_actions(self.article, self.request.user)
        }
        return action in actions if action else bool(actions & {"approve", "reject"})

    def perform_review_action(self, action, comment, expected_revision_id):
        with transaction.atomic():
            article = ArticlePage.objects.select_for_update().get(pk=self.article.pk)
            latest_revision = article.get_latest_revision()
            if str(latest_revision.pk if latest_revision else "") != str(
                expected_revision_id
            ):
                raise ArticleRevisionConflict(
                    "文章在审核页面打开后已产生新 revision，请刷新后重新审核。"
                )
            task = article.current_workflow_task
            task_state = article.current_workflow_task_state
            if task and task_state:
                self.close_wagtail_review_task_without_publishing(
                    task_state,
                    action,
                    comment,
                )
            if action == "approve":
                article.approve(
                    self.request.user,
                    comment,
                    expected_revision_id=expected_revision_id,
                )
            else:
                article.reject(
                    self.request.user,
                    comment,
                    expected_revision_id=expected_revision_id,
                )

    def close_wagtail_review_task_without_publishing(self, task_state, action, comment):
        """Close Wagtail's moderation task without publishing the Page.

        Wagtail's default workflow approval publishes the page revision when the
        final task is approved. AI Author Forum keeps review approval separate
        from placement and static publishing, so the business review page only
        marks the task/workflow as completed and lets ArticlePage.approve/reject
        record the project-specific review state and audit trail.
        """

        workflow_state = task_state.workflow_state
        if action == "approve":
            task_state.approve(
                user=self.request.user,
                update=False,
                comment=comment,
            )
            workflow_state.status = workflow_state.STATUS_APPROVED
        else:
            task_state.reject(
                user=self.request.user,
                update=False,
                comment=comment,
            )
            workflow_state.status = workflow_state.STATUS_NEEDS_CHANGES
        workflow_state.save(update_fields=["status"])

    def get_success_url(self):
        return reverse("article_admin:review_detail", args=[self.article.pk])


@method_decorator(xframe_options_sameorigin, name="dispatch")
class ArticleReviewPreviewView(ArticleReviewPermissionMixin, View):
    def get(self, request, page_id):
        article = get_object_or_404(
            Page.objects.select_related("latest_revision"), pk=page_id
        ).specific
        if not isinstance(article, ArticlePage):
            raise Http404
        latest_revision = article.get_latest_revision()
        if latest_revision:
            article = latest_revision.as_object()
        return article.serve_preview(request, article.default_preview_mode)


def get_revision_diff(article):
    revisions = list(article.revisions.order_by("-created_at", "-id")[:2])
    current_revision = revisions[0] if revisions else None
    previous_revision = revisions[1] if len(revisions) > 1 else None
    if not current_revision:
        return {
            "has_previous_revision": False,
            "current_revision": None,
            "previous_revision": None,
            "groups": [],
            "changed_count": 0,
        }

    current_object = current_revision.as_object()
    previous_object = previous_revision.as_object() if previous_revision else None
    groups = []
    changed_count = 0
    for key, label, field_defs in ARTICLE_DIFF_GROUPS:
        fields = [
            get_field_diff(previous_object, current_object, field_name, field_label)
            for field_name, field_label in field_defs
        ]
        changed_fields = [field for field in fields if field["changed"]]
        changed_count += len(changed_fields)
        groups.append(
            {
                "key": key,
                "label": label,
                "fields": fields,
                "changed_fields": changed_fields,
            }
        )
    return {
        "has_previous_revision": previous_revision is not None,
        "current_revision": current_revision,
        "previous_revision": previous_revision,
        "groups": groups,
        "changed_count": changed_count,
    }


def get_field_diff(previous_object, current_object, field_name, label):
    previous_raw = getattr(previous_object, field_name, "") if previous_object else ""
    current_raw = getattr(current_object, field_name, "")
    if field_name == "body":
        previous_blocks = summarise_stream_blocks(previous_raw)
        current_blocks = summarise_stream_blocks(current_raw)
        return {
            "field": field_name,
            "label": label,
            "previous": previous_blocks,
            "current": current_blocks,
            "changed": previous_blocks != current_blocks,
            "is_streamfield": True,
            "diff": "",
        }

    previous_value = serialise_field_value(previous_raw)
    current_value = serialise_field_value(current_raw)
    diff_lines = []
    if previous_value != current_value:
        diff_lines = list(
            difflib.unified_diff(
                previous_value.splitlines(),
                current_value.splitlines(),
                fromfile="上一版本",
                tofile="当前版本",
                lineterm="",
            )
        )
    return {
        "field": field_name,
        "label": label,
        "previous": previous_value,
        "current": current_value,
        "changed": previous_value != current_value,
        "is_streamfield": False,
        "diff": "\n".join(diff_lines),
    }


def summarise_stream_blocks(value):
    raw_data = getattr(value, "raw_data", value) or []
    summaries = []
    for index, block in enumerate(raw_data, start=1):
        if not isinstance(block, dict):
            summaries.append(f"{index}. 未知块：{str(block)[:80]}")
            continue
        block_type = block.get("type", "unknown")
        block_value = block.get("value")
        summaries.append(f"{index}. {block_type}：{_block_value_summary(block_value)}")
    return summaries


def _block_value_summary(value):
    if isinstance(value, dict):
        for key in ("caption", "quote", "text", "title"):
            if value.get(key):
                return str(value[key]).replace("\n", " ")[:160]
        return "、".join(str(key) for key in value.keys())[:160]
    if isinstance(value, list):
        return f"{len(value)} 项"
    text = str(value or "").replace("\n", " ").strip()
    return text[:160] or "（空）"


def serialise_field_value(value):
    if value is None:
        return ""
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value)
