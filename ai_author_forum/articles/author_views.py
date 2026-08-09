from __future__ import annotations

from uuid import uuid4

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from ai_author_forum.journals.models import Journal
from ai_author_forum.journals.submission_services import (
    journal_accepts_author_submission,
    public_submission_journals,
)
from ai_author_forum.site_settings.access_control import (
    can_access_author_workbench,
    can_edit_submission,
    can_submit_submission,
    filter_author_submissions,
    get_article_authorship,
    get_journal_editor_assignment,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.users.auth_scope import (
    AUTHOR_SCOPE,
    safe_internal_next,
    set_login_scope,
)

from .author_forms import (
    AuthorContributorFormSet,
    AuthorshipGrantForm,
    AuthorshipRevokeForm,
    AuthorSubmissionCreateForm,
    AuthorSubmissionFieldsForm,
    ChangeSubmissionJournalForm,
    ControlledTransferForm,
    SubmitAuthorSubmissionForm,
    contributor_initial,
)
from .author_services import (
    change_author_submission_journal,
    controlled_transfer_submission,
    create_author_submission,
    grant_article_authorship,
    revoke_article_authorship,
    save_author_submission,
    submit_author_submission,
    validate_author_submission,
)
from .models import ArticlePage, ArticleReviewRecord

AUTHOR_RATE_LIMIT = 30
AUTHOR_RATE_WINDOW = 60


def _request_id(request):
    return (
        request.POST.get("request_id") or request.headers.get("X-Request-ID") or uuid4()
    )


def _rate_limit(request):
    if request.method != "POST" or not request.user.is_authenticated:
        return False
    key = f"author-write:{request.user.pk}:{request.path}"
    count = cache.get(key, 0)
    if count >= AUTHOR_RATE_LIMIT:
        operation_source = (
            "author_workbench"
            if request.path.startswith(reverse("author:dashboard"))
            else "editor_admin"
        )
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.FAILURE,
            actor=request.user,
            target_type="AuthorSubmissionWrite",
            target_id=request.path,
            message="作者投稿相关写操作触发速率限制。",
            request_id=str(_request_id(request)),
            metadata={
                "operation_source": operation_source,
                "result": "denied",
                "reason": "rate_limit",
                "path": request.path,
            },
        )
        return True
    cache.set(key, count + 1, AUTHOR_RATE_WINDOW)
    return False


def _service_error_status(exc):
    if isinstance(exc, PermissionDenied):
        return 403
    if exc.__class__.__name__.endswith("Conflict"):
        return 409
    return 400


def _audit_rejected_request(
    request,
    *,
    target=None,
    message,
    status_code,
    forms=(),
    operation_source="author_workbench",
):
    invalid_fields = set()
    for form in forms:
        errors = getattr(form, "errors", {})
        if hasattr(errors, "keys"):
            invalid_fields.update(errors.keys())
        else:
            for form_errors in errors:
                if hasattr(form_errors, "keys"):
                    invalid_fields.update(form_errors.keys())
        non_form_errors = getattr(form, "non_form_errors", None)
        if non_form_errors is not None and non_form_errors():
            invalid_fields.add("__all__")
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.FAILURE,
        actor=request.user if request.user.is_authenticated else None,
        target=target,
        target_type=(
            target.__class__.__name__ if target is not None else "AuthorSubmission"
        ),
        target_id=str(getattr(target, "pk", "")),
        message=message,
        request_id=str(_request_id(request)),
        metadata={
            "operation_source": operation_source,
            "result": "failure",
            "http_status": status_code,
            "invalid_fields": sorted(invalid_fields),
        },
    )


def _posted_journal(request, field_name):
    value = str(request.POST.get(field_name) or "")
    if not value.isdigit():
        return None
    return Journal.objects.filter(pk=int(value)).first()


def _safe_next(request, fallback):
    value = request.POST.get("next") or request.GET.get("next")
    return safe_internal_next(request, value, scope=AUTHOR_SCOPE) or fallback


def _author_article_or_404(request, article_id):
    article = (
        filter_author_submissions(
            request.user,
            ArticlePage.objects.select_related("primary_journal").prefetch_related(
                "contributors", "category_assignments__category", "authorships"
            ),
        )
        .filter(pk=article_id)
        .first()
    )
    if article is None:
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.FAILURE,
            actor=request.user if request.user.is_authenticated else None,
            target_type="ArticlePage",
            target_id=str(article_id),
            message="作者访问不存在或无权访问的投稿。",
            request_id=str(_request_id(request)),
            metadata={"operation_source": "author_workbench", "result": "denied"},
        )
        raise Http404
    return article


class AuthorLoginView(View):
    template_name = "author/login.html"

    def get(self, request):
        if request.user.is_authenticated and request.user.must_change_password:
            return redirect("account:change_password")
        if request.user.is_authenticated and can_access_author_workbench(request.user):
            return redirect("author:dashboard")
        next_url = _safe_next(request, reverse("author:dashboard"))
        return render(
            request,
            self.template_name,
            {"form": AuthenticationForm(request), "next": next_url},
        )

    def post(self, request):
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if (
                not user.is_active
                or not getattr(user, "is_author", False)
                or not can_access_author_workbench(user)
            ):
                form.add_error(
                    None, "无法使用作者投稿入口登录，请检查账号或联系管理员。"
                )
                AuditLog.record(
                    action=AuditAction.PERMISSION,
                    status=AuditStatus.FAILURE,
                    actor=user,
                    target=user,
                    message="作者入口登录被拒绝。",
                    metadata={"operation_source": "author_login", "result": "denied"},
                )
            else:
                login(request, user)
                set_login_scope(request, AUTHOR_SCOPE)
                if user.must_change_password:
                    return redirect("account:change_password")
                return redirect(_safe_next(request, reverse("author:dashboard")))
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "next": _safe_next(request, reverse("author:dashboard")),
            },
        )


class AuthorLogoutView(View):
    def post(self, request):
        logout(request)
        return redirect("author:login")


class AuthorRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('author:login')}?next={request.get_full_path()}")
        if request.user.must_change_password:
            return redirect("account:change_password")
        if not can_access_author_workbench(request.user):
            AuditLog.record(
                action=AuditAction.PERMISSION,
                status=AuditStatus.FAILURE,
                actor=request.user,
                target=request.user,
                message="作者工作台入口被拒绝。",
                request_id=str(_request_id(request)),
                metadata={"operation_source": "author_workbench", "result": "denied"},
            )
            return HttpResponseForbidden("该账号没有有效作者投稿关系。")
        if _rate_limit(request):
            return HttpResponse("作者写操作过于频繁，请稍后再试。", status=429)
        return super().dispatch(request, *args, **kwargs)


class AuthorDashboardView(AuthorRequiredMixin, TemplateView):
    template_name = "author/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        articles = list(
            filter_author_submissions(
                self.request.user,
                ArticlePage.objects.select_related("primary_journal").prefetch_related(
                    "category_assignments__category", "authorships", "review_records"
                ),
            ).order_by("-last_submitted_at", "-latest_revision_created_at", "-pk")
        )
        for article in articles:
            authorship = get_article_authorship(self.request.user, article)
            article.authorship_for_user = authorship
            article.author_can_edit = can_edit_submission(self.request.user, article)
            article.author_can_submit = can_submit_submission(
                self.request.user, article
            )
            article.latest_author_feedback = next(
                (
                    item
                    for item in article.review_records.all()
                    if item.author_visible_comment
                ),
                None,
            )
        context.update(
            {
                "articles": articles,
                "journals_url": reverse("author:journals"),
                "new_submission_url": reverse("author:new"),
            }
        )
        return context


class AuthorJournalListView(AuthorRequiredMixin, TemplateView):
    template_name = "author/journals.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["journals"] = public_submission_journals()
        context["new_submission_url"] = reverse("author:new")
        return context


class AuthorSubmissionCreateView(AuthorRequiredMixin, View):
    template_name = "author/submission_form.html"

    def get(self, request):
        selected_journal = None
        selected_journal_id = request.GET.get("journal")
        if selected_journal_id:
            try:
                selected_journal = (
                    public_submission_journals()
                    .filter(pk=int(selected_journal_id))
                    .first()
                )
            except (TypeError, ValueError):
                selected_journal = None
        return render(
            request,
            self.template_name,
            {
                "form": AuthorSubmissionCreateForm(journal=selected_journal),
                "formset": AuthorContributorFormSet(
                    prefix="contributors",
                    initial=contributor_initial(user=request.user),
                ),
                "mode": "create",
            },
        )

    def post(self, request):
        posted_journal = _posted_journal(request, "journal")
        if posted_journal is not None and not journal_accepts_author_submission(
            posted_journal
        ):
            AuditLog.record(
                action=AuditAction.PERMISSION,
                status=AuditStatus.FAILURE,
                actor=request.user,
                target=posted_journal,
                message="作者创建投稿时选择了不可投稿期刊。",
                request_id=str(_request_id(request)),
                metadata={"operation_source": "author_workbench", "result": "denied"},
            )
            return HttpResponse("目标期刊当前不接受作者投稿。", status=403)
        form = AuthorSubmissionCreateForm(request.POST, request.FILES)
        formset = AuthorContributorFormSet(request.POST, prefix="contributors")
        form_is_valid = form.is_valid()
        formset_is_valid = formset.is_valid()
        if form_is_valid and formset_is_valid:
            try:
                article = create_author_submission(
                    actor=request.user,
                    journal=form.cleaned_data["journal"],
                    category=form.cleaned_data["category"],
                    fields=form.service_fields(),
                    contributors=formset.service_rows(),
                    cover_file=form.cleaned_data.get("cover_image"),
                    body_uploads=form.body_uploads(),
                    request_id=form.cleaned_data["request_id"],
                )
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, "；".join(getattr(exc, "messages", [str(exc)])))
            else:
                return redirect("author:detail", article.pk)
        else:
            _audit_rejected_request(
                request,
                target=posted_journal,
                message="作者创建投稿表单校验失败。",
                status_code=400,
                forms=(form, formset),
            )
        return render(
            request,
            self.template_name,
            {"form": form, "formset": formset, "mode": "create"},
            status=400,
        )


class AuthorSubmissionDetailView(AuthorRequiredMixin, TemplateView):
    template_name = "author/submission_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        article = _author_article_or_404(self.request, kwargs["article_id"])
        authorship = get_article_authorship(self.request.user, article)
        latest_revision = article.get_latest_revision()
        feedback = article.review_records.filter(
            author_visible_comment__gt=""
        ).order_by("created_at", "pk")
        if feedback.exists():
            AuditLog.record(
                action=AuditAction.PERMISSION,
                status=AuditStatus.SUCCESS,
                actor=self.request.user,
                target=article,
                message="作者查看本人投稿公开审核意见。",
                request_id=str(_request_id(self.request)),
                metadata={
                    "operation_source": "author_workbench",
                    "result": "success",
                    "author_visible_feedback_count": feedback.count(),
                },
            )
        context.update(
            {
                "article": article,
                "authorship": authorship,
                "latest_revision": latest_revision,
                "feedback": feedback,
                "can_edit": can_edit_submission(self.request.user, article),
                "can_submit": can_submit_submission(self.request.user, article),
                "edit_url": reverse("author:edit", args=[article.pk]),
                "submit_url": reverse("author:submit", args=[article.pk]),
                "change_journal_url": reverse(
                    "author:change_journal", args=[article.pk]
                ),
                "history_url": reverse("author:history", args=[article.pk]),
            }
        )
        return context


class AuthorSubmissionEditView(AuthorRequiredMixin, View):
    template_name = "author/submission_form.html"

    def get(self, request, article_id):
        article = _author_article_or_404(request, article_id)
        if not can_edit_submission(request.user, article):
            return HttpResponse("该投稿当前已锁定，不能编辑。", status=409)
        return render(
            request,
            self.template_name,
            {
                "form": AuthorSubmissionFieldsForm(article=article),
                "formset": AuthorContributorFormSet(
                    prefix="contributors", initial=contributor_initial(article=article)
                ),
                "mode": "edit",
                "article": article,
            },
        )

    def post(self, request, article_id):
        article = _author_article_or_404(request, article_id)
        form = AuthorSubmissionFieldsForm(request.POST, request.FILES, article=article)
        formset = AuthorContributorFormSet(request.POST, prefix="contributors")
        form_is_valid = form.is_valid()
        formset_is_valid = formset.is_valid()
        if form_is_valid and formset_is_valid:
            try:
                save_author_submission(
                    actor=request.user,
                    article=article,
                    expected_revision_id=form.cleaned_data["expected_revision_id"],
                    fields=form.service_fields(),
                    contributors=formset.service_rows(),
                    category=form.cleaned_data["category"],
                    cover_file=form.cleaned_data.get("cover_image"),
                    remove_cover=form.cleaned_data.get("remove_cover", False),
                    body_uploads=form.body_uploads(),
                    request_id=form.cleaned_data["request_id"],
                )
            except (PermissionDenied, ValidationError) as exc:
                status = _service_error_status(exc)
                form.add_error(None, "；".join(getattr(exc, "messages", [str(exc)])))
                return render(
                    request,
                    self.template_name,
                    {
                        "form": form,
                        "formset": formset,
                        "mode": "edit",
                        "article": article,
                    },
                    status=status,
                )
            return redirect("author:detail", article.pk)
        _audit_rejected_request(
            request,
            target=article,
            message="作者保存投稿表单校验失败。",
            status_code=400,
            forms=(form, formset),
        )
        return render(
            request,
            self.template_name,
            {"form": form, "formset": formset, "mode": "edit", "article": article},
            status=400,
        )


class AuthorSubmissionChangeJournalView(AuthorRequiredMixin, View):
    template_name = "author/change_journal.html"

    def get(self, request, article_id):
        article = _author_article_or_404(request, article_id)
        if (
            not can_edit_submission(request.user, article)
            or article.has_ever_been_submitted
            or get_article_authorship(request.user, article).role != "owner"
        ):
            return HttpResponse("该投稿当前不能由作者更换主投期刊。", status=409)
        latest = article.get_latest_revision()
        return render(
            request,
            self.template_name,
            {
                "article": article,
                "form": ChangeSubmissionJournalForm(
                    current_journal=article.primary_journal,
                    initial={"expected_revision_id": latest.pk if latest else ""},
                ),
            },
        )

    def post(self, request, article_id):
        article = _author_article_or_404(request, article_id)
        posted_journal = _posted_journal(request, "target_journal")
        if posted_journal is not None and not journal_accepts_author_submission(
            posted_journal
        ):
            _audit_rejected_request(
                request,
                target=article,
                message="作者更换主投期刊时选择了不可投稿期刊。",
                status_code=403,
            )
            return HttpResponse("目标期刊当前不接受作者投稿。", status=403)
        form = ChangeSubmissionJournalForm(
            request.POST, current_journal=article.primary_journal
        )
        if form.is_valid():
            try:
                change_author_submission_journal(
                    actor=request.user,
                    article=article,
                    target_journal=form.cleaned_data["target_journal"],
                    target_category=form.cleaned_data["target_category"],
                    expected_revision_id=form.cleaned_data["expected_revision_id"],
                    request_id=form.cleaned_data["request_id"],
                )
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, "；".join(getattr(exc, "messages", [str(exc)])))
                return render(
                    request,
                    self.template_name,
                    {"article": article, "form": form},
                    status=_service_error_status(exc),
                )
            else:
                return redirect("author:detail", article.pk)
        _audit_rejected_request(
            request,
            target=article,
            message="作者更换主投期刊表单校验失败。",
            status_code=400,
            forms=(form,),
        )
        return render(
            request, self.template_name, {"article": article, "form": form}, status=400
        )


class AuthorSubmissionSubmitView(AuthorRequiredMixin, View):
    template_name = "author/submit_confirm.html"

    def get(self, request, article_id):
        article = _author_article_or_404(request, article_id)
        if not can_submit_submission(request.user, article):
            return HttpResponse("该投稿当前不能提交初审。", status=409)
        latest = article.get_latest_revision()
        validation_errors = {}
        try:
            validate_author_submission(article, latest)
        except ValidationError as exc:
            validation_errors = getattr(exc, "message_dict", None) or {
                "submission": exc.messages
            }
        return render(
            request,
            self.template_name,
            {
                "article": article,
                "latest_revision": latest,
                "validation_errors": validation_errors,
                "form": SubmitAuthorSubmissionForm(
                    initial={
                        "expected_revision_id": latest.pk if latest else "",
                        "request_id": uuid4(),
                    }
                ),
            },
        )

    def post(self, request, article_id):
        article = _author_article_or_404(request, article_id)
        form = SubmitAuthorSubmissionForm(request.POST)
        if form.is_valid():
            try:
                submit_author_submission(
                    actor=request.user,
                    article=article,
                    expected_revision_id=form.cleaned_data["expected_revision_id"],
                    request_id=form.cleaned_data["request_id"],
                    comment=form.cleaned_data.get("comment", ""),
                )
            except PermissionDenied as exc:
                return HttpResponse(str(exc), status=403)
            except ValidationError as exc:
                form.add_error(None, "；".join(getattr(exc, "messages", [str(exc)])))
                return render(
                    request,
                    self.template_name,
                    {
                        "article": article,
                        "latest_revision": article.get_latest_revision(),
                        "form": form,
                    },
                    status=_service_error_status(exc),
                )
            else:
                return redirect("author:detail", article.pk)
        _audit_rejected_request(
            request,
            target=article,
            message="作者提交初审表单校验失败。",
            status_code=400,
            forms=(form,),
        )
        return render(
            request,
            self.template_name,
            {
                "article": article,
                "latest_revision": article.get_latest_revision(),
                "form": form,
            },
            status=400,
        )


class AuthorSubmissionHistoryView(AuthorRequiredMixin, TemplateView):
    template_name = "author/submission_history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        article = _author_article_or_404(self.request, kwargs["article_id"])
        records = article.review_records.filter(
            Q(action=ArticleReviewRecord.Action.SUBMIT)
            | Q(author_visible_comment__gt="")
        ).order_by("created_at", "pk")
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.SUCCESS,
            actor=self.request.user,
            target=article,
            message="作者查看本人投稿审核意见。",
            request_id=str(_request_id(self.request)),
            metadata={"operation_source": "author_workbench", "result": "success"},
        )
        context.update({"article": article, "records": records})
        return context


def _can_manage_authorship(user, article):
    if is_super_admin(user):
        return True
    assignment = get_journal_editor_assignment(user, article.primary_journal)
    return bool(assignment and assignment.role == "chief_editor")


def _require_manage_authorship(request, article):
    if _can_manage_authorship(request.user, article):
        return
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.FAILURE,
        actor=request.user,
        target=article,
        message="投稿关系或受控转投管理被拒绝。",
        request_id=str(_request_id(request)),
        metadata={"operation_source": "editor_admin", "result": "denied"},
    )
    raise PermissionDenied


class AdminArticleAuthorshipView(View):
    template_name = "wagtailadmin/articles/authorships.html"

    def _article(self, request, article_id):
        article = get_object_or_404(
            ArticlePage.objects.select_related("primary_journal"), pk=article_id
        )
        _require_manage_authorship(request, article)
        return article

    def get(self, request, article_id):
        article = self._article(request, article_id)
        return render(
            request,
            self.template_name,
            {
                "article": article,
                "authorships": article.authorships.select_related("user"),
                "grant_form": AuthorshipGrantForm(),
            },
        )

    def post(self, request, article_id):
        if _rate_limit(request):
            return HttpResponse("关系变更操作过于频繁，请稍后再试。", status=429)
        article = self._article(request, article_id)
        action = request.POST.get("action", "grant")
        if action == "revoke":
            authorship = get_object_or_404(
                article.authorships, pk=request.POST.get("authorship_id")
            )
            form = AuthorshipRevokeForm(request.POST)
            if form.is_valid():
                try:
                    revoke_article_authorship(
                        actor=request.user,
                        authorship=authorship,
                        reason=form.cleaned_data["reason"],
                        request_id=form.cleaned_data["request_id"],
                    )
                except (PermissionDenied, ValidationError) as exc:
                    form.add_error(
                        None, "；".join(getattr(exc, "messages", [str(exc)]))
                    )
                else:
                    messages.success(request, "投稿关系已撤销。")
                    return redirect("article_admin:authorships", article.pk)
        else:
            form = AuthorshipGrantForm(request.POST)
            if form.is_valid():
                try:
                    grant_article_authorship(
                        actor=request.user,
                        article=article,
                        user=form.cleaned_data["user"],
                        role=form.cleaned_data["role"],
                        can_edit=form.cleaned_data["can_edit"],
                        is_corresponding=form.cleaned_data["is_corresponding"],
                        request_id=form.cleaned_data["request_id"],
                    )
                except (PermissionDenied, ValidationError) as exc:
                    form.add_error(
                        None, "；".join(getattr(exc, "messages", [str(exc)]))
                    )
                else:
                    messages.success(request, "投稿关系已保存。")
                    return redirect("article_admin:authorships", article.pk)
        if not form.is_valid():
            _audit_rejected_request(
                request,
                target=article,
                message="投稿关系管理表单校验失败。",
                status_code=400,
                forms=(form,),
                operation_source="editor_admin",
            )
        return render(
            request,
            self.template_name,
            {
                "article": article,
                "authorships": article.authorships.select_related("user"),
                "grant_form": form,
            },
            status=400,
        )


class AdminControlledTransferView(View):
    template_name = "wagtailadmin/articles/controlled_transfer.html"

    def get(self, request, article_id):
        article = get_object_or_404(
            ArticlePage.objects.select_related("primary_journal"), pk=article_id
        )
        _require_manage_authorship(request, article)
        latest = article.get_latest_revision()
        return render(
            request,
            self.template_name,
            {
                "article": article,
                "form": ControlledTransferForm(
                    current_journal=article.primary_journal,
                    initial={
                        "expected_state": article.review_status,
                        "expected_revision_id": latest.pk if latest else "",
                    },
                ),
            },
        )

    def post(self, request, article_id):
        if _rate_limit(request):
            return HttpResponse("转投操作过于频繁，请稍后再试。", status=429)
        article = get_object_or_404(
            ArticlePage.objects.select_related("primary_journal"), pk=article_id
        )
        _require_manage_authorship(request, article)
        posted_journal = _posted_journal(request, "target_journal")
        if posted_journal is not None and not journal_accepts_author_submission(
            posted_journal
        ):
            _audit_rejected_request(
                request,
                target=article,
                message="受控转投时选择了不可投稿期刊。",
                status_code=403,
                operation_source="editor_admin",
            )
            return HttpResponse("目标期刊当前不接受作者投稿。", status=403)
        form = ControlledTransferForm(
            request.POST, current_journal=article.primary_journal
        )
        if form.is_valid():
            try:
                controlled_transfer_submission(
                    actor=request.user,
                    article=article,
                    target_journal=form.cleaned_data["target_journal"],
                    target_category=form.cleaned_data["target_category"],
                    reason=form.cleaned_data["reason"],
                    expected_state=form.cleaned_data["expected_state"],
                    expected_revision_id=form.cleaned_data["expected_revision_id"],
                    request_id=form.cleaned_data["request_id"],
                )
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, "；".join(getattr(exc, "messages", [str(exc)])))
                return render(
                    request,
                    self.template_name,
                    {"article": article, "form": form},
                    status=_service_error_status(exc),
                )
            else:
                messages.success(request, "文章已转入目标期刊初审队列。")
                return redirect("article_admin:review_detail", article.pk)
        _audit_rejected_request(
            request,
            target=article,
            message="受控转投表单校验失败。",
            status_code=400,
            forms=(form,),
            operation_source="editor_admin",
        )
        return render(
            request,
            self.template_name,
            {"article": article, "form": form},
            status=400,
        )
