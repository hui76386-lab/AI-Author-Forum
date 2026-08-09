from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from ai_author_forum.site_settings.access_control import can_manage_accounts
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from .auth_scope import (
    ADMIN_SCOPE,
    AUTHOR_SCOPE,
    clear_login_scope,
    get_login_scope,
)
from .forms import (
    AccountCreateForm,
    AccountStatusForm,
    JournalAssignmentFormSet,
    RequiredPasswordChangeForm,
    ResetPasswordForm,
)
from .middleware import (
    clear_credential_failures,
    credential_is_limited,
    record_credential_failure,
    request_ip,
)
from .services import (
    activate_account,
    create_account,
    deactivate_account,
    reset_account_password,
    suspend_account,
)


class SuperAdminRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not can_manage_accounts(request.user):
            return HttpResponseForbidden("无权管理账号。")
        return super().dispatch(request, *args, **kwargs)


class AccountListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "users/admin/account_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()
        status = self.request.GET.get("status", "")
        users = User.objects.prefetch_related(
            "groups", "journal_editor_assignments", "article_authorships"
        )
        if status in User.AccountStatus.values:
            users = users.filter(account_status=status)
        context.update(
            {
                "title": "账号管理",
                "accounts": users.order_by("display_name", "username"),
                "status": status,
                "new_account_url": reverse("account_admin:new"),
            }
        )
        return context


class AccountCreateView(SuperAdminRequiredMixin, TemplateView):
    template_name = "users/admin/account_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", AccountCreateForm())
        context.setdefault(
            "assignment_formset", JournalAssignmentFormSet(prefix="roles")
        )
        context["title"] = "新建账号"
        return context

    def post(self, request):
        form = AccountCreateForm(request.POST)
        assignment_formset = JournalAssignmentFormSet(request.POST, prefix="roles")
        is_super_admin_account = request.POST.get("is_super_admin_account") == "on"
        is_author_account = request.POST.get("is_author_account") == "on"
        forms_valid = form.is_valid() and assignment_formset.is_valid()
        assignments = []
        if forms_valid:
            assignments = [
                row.as_service_payload()
                for row in assignment_formset.forms
                if row.cleaned_data and not row.cleaned_data.get("DELETE")
            ]
            if is_super_admin_account and assignments:
                form.add_error(
                    "is_super_admin_account",
                    "超级管理员不能同时分配子期刊角色。",
                )
                forms_valid = False
            elif (
                not is_super_admin_account and not assignments and not is_author_account
            ):
                form.add_error(None, "账号必须分配作者、编辑或超级管理员角色。")
                forms_valid = False
        if forms_valid:
            try:
                user = create_account(
                    actor=request.user,
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data["email"],
                    display_name=form.cleaned_data["display_name"],
                    institution=form.cleaned_data["institution"],
                    job_title=form.cleaned_data["job_title"],
                    temporary_password=form.cleaned_data["temporary_password"],
                    is_super_admin_account=is_super_admin_account,
                    is_author_account=is_author_account,
                    assignments=assignments,
                    confirming_password=form.cleaned_data["confirming_password"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "账号和角色任命已创建。")
                return redirect("account_admin:detail", user.pk)
        return self.render_to_response(
            self.get_context_data(form=form, assignment_formset=assignment_formset)
        )


class AccountDetailView(SuperAdminRequiredMixin, TemplateView):
    template_name = "users/admin/account_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = get_object_or_404(
            get_user_model().objects.prefetch_related(
                "groups",
                "journal_editor_assignments__journal",
                "article_authorships__article",
            ),
            pk=kwargs["account_id"],
        )
        context.update(
            {
                "title": account.display_name,
                "account": account,
                "status_form": AccountStatusForm(),
                "reset_form": ResetPasswordForm(user=account),
            }
        )
        return context


class AccountStatusView(SuperAdminRequiredMixin, View):
    action = ""

    def post(self, request, account_id):
        account = get_object_or_404(get_user_model(), pk=account_id)
        form = AccountStatusForm(request.POST)
        if not form.is_valid():
            messages.error(request, "账号状态表单不完整。")
            return redirect("account_admin:detail", account.pk)
        service = {
            "suspend": suspend_account,
            "deactivate": deactivate_account,
        }[self.action]
        try:
            service(
                actor=request.user,
                user=account,
                reason=form.cleaned_data["reason"],
                confirming_password=form.cleaned_data["confirming_password"],
            )
        except ValidationError as exc:
            messages.error(request, "；".join(exc.messages))
        else:
            messages.success(request, "账号状态已更新，旧会话已撤销。")
        return redirect("account_admin:detail", account.pk)


class AccountActivateView(SuperAdminRequiredMixin, View):
    def post(self, request, account_id):
        account = get_object_or_404(get_user_model(), pk=account_id)
        try:
            activate_account(actor=request.user, user=account)
        except ValidationError as exc:
            messages.error(request, "；".join(exc.messages))
        else:
            messages.success(request, "账号已恢复。")
        return redirect("account_admin:detail", account.pk)


class AccountResetPasswordView(SuperAdminRequiredMixin, TemplateView):
    template_name = "users/admin/account_reset_password.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = get_object_or_404(
            get_user_model(),
            pk=kwargs["account_id"],
        )
        requires_confirmation = account.groups.filter(name="超级管理员").exists()
        context.setdefault(
            "form",
            ResetPasswordForm(
                user=account,
                requires_confirmation=requires_confirmation,
            ),
        )
        context.update(
            {
                "title": "重置临时密码",
                "account": account,
            }
        )
        return context

    def post(self, request, account_id):
        account = get_object_or_404(get_user_model(), pk=account_id)
        ip_address = request_ip(request)
        if credential_is_limited("reset", account.pk, ip_address):
            return HttpResponse("密码重置尝试过多，请稍后再试。", status=429)
        requires_confirmation = account.groups.filter(name="超级管理员").exists()
        form = ResetPasswordForm(
            request.POST,
            user=account,
            requires_confirmation=requires_confirmation,
        )
        if form.is_valid():
            try:
                reset_account_password(
                    actor=request.user,
                    user=account,
                    temporary_password=form.cleaned_data["temporary_password"],
                    confirming_password=form.cleaned_data.get(
                        "confirming_password", ""
                    ),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                clear_credential_failures("reset", account.pk, ip_address)
                messages.success(request, "临时密码已重置，旧会话已撤销。")
                return redirect("account_admin:detail", account.pk)
        if form.errors:
            record_credential_failure(
                "reset",
                account.pk,
                ip_address,
                actor=request.user,
                target=account,
            )
            messages.error(request, "临时密码不符合密码策略。")
        return self.render_to_response(
            self.get_context_data(account_id=account.pk, form=form)
        )


class LegacyRequiredPasswordChangeRedirectView(View):
    """Compatibility endpoint; the actual form lives outside Wagtail."""

    def dispatch(self, request, *args, **kwargs):
        return redirect("account:change_password")


def _password_change_destination(request, user):
    scope = get_login_scope(request)
    if scope == AUTHOR_SCOPE:
        from ai_author_forum.site_settings.access_control import (
            can_access_author_workbench,
        )

        if can_access_author_workbench(user):
            return "author:dashboard"
    if (scope == ADMIN_SCOPE and getattr(user, "is_staff", False)) or (
        scope is None and getattr(user, "is_staff", False)
    ):
        return "wagtailadmin_home"
    from ai_author_forum.site_settings.access_control import can_access_author_workbench

    return (
        "author:dashboard"
        if can_access_author_workbench(user)
        else "wagtailadmin_login"
    )


class RequiredPasswordChangeView(TemplateView):
    template_name = "account/change_password.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(
                f"{reverse('wagtailadmin_login')}?next={reverse('account:change_password')}"
            )
        if (
            not request.user.is_active
            or request.user.account_status != request.user.AccountStatus.ACTIVE
        ):
            logout(request)
            return redirect("wagtailadmin_login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", RequiredPasswordChangeForm(self.request.user))
        context["title"] = "修改密码"
        context["login_scope"] = get_login_scope(self.request)
        return context

    def post(self, request):
        ip_address = request_ip(request)
        if credential_is_limited("first_change", request.user.pk, ip_address):
            return HttpResponse("密码修改尝试过多，请稍后再试。", status=429)
        form = RequiredPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                user.must_change_password = False
                user.save(update_fields=["must_change_password"])
                AuditLog.record(
                    action=AuditAction.PERMISSION,
                    status=AuditStatus.SUCCESS,
                    actor=user,
                    target=user,
                    message="完成首次强制密码修改。",
                    metadata={
                        "operation_source": "account_password_change",
                        "result": "success",
                        "login_scope": get_login_scope(request),
                    },
                )
            update_session_auth_hash(request, user)
            clear_credential_failures("first_change", user.pk, ip_address)
            messages.success(request, "密码已更新。")
            destination = _password_change_destination(request, user)
            clear_login_scope(request)
            return redirect(destination)
        record_credential_failure(
            "first_change",
            request.user.pk,
            ip_address,
            actor=request.user,
            target=request.user,
        )
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.FAILURE,
            actor=request.user,
            target=request.user,
            message="首次强制密码修改失败。",
            metadata={
                "operation_source": "account_password_change",
                "result": "failure",
                "invalid_fields": sorted(form.errors.keys()),
            },
            ip_address=ip_address,
        )
        return self.render_to_response(self.get_context_data(form=form))
