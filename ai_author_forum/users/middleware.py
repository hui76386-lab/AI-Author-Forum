from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render

from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from .auth_scope import ADMIN_SCOPE, clear_login_scope, set_login_scope

MAX_CREDENTIAL_ATTEMPTS = 5
CREDENTIAL_COOLDOWN_SECONDS = 900
PASSWORD_CHANGE_RESOURCE_PATHS = frozenset(
    {
        "/admin/jsi18n/",
        "/admin/sprite/",
    }
)
NEUTRAL_PASSWORD_CHANGE_PATH = "/account/change-password/"
LEGACY_PASSWORD_CHANGE_PATH = "/admin/accounts/change-password/"
AUTHOR_FORBIDDEN_PREFIXES = ("/admin/", "/django-admin/", "/documents/")


def request_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",", 1)[0].strip() if forwarded else None
    ) or request.META.get("REMOTE_ADDR")


def _credential_keys(kind, account_key, ip_address):
    account_key = str(account_key or "unknown").strip().lower()
    return (
        f"credential:{kind}:account:{account_key}",
        f"credential:{kind}:ip:{ip_address or 'unknown'}",
    )


def credential_is_limited(kind, account_key, ip_address):
    return any(
        cache.get(key, 0) >= MAX_CREDENTIAL_ATTEMPTS
        for key in _credential_keys(kind, account_key, ip_address)
    )


def clear_credential_failures(kind, account_key, ip_address):
    cache.delete_many(_credential_keys(kind, account_key, ip_address))


def record_credential_failure(
    kind, account_key, ip_address, *, actor=None, target=None
):
    attempts = []
    for key in _credential_keys(kind, account_key, ip_address):
        value = cache.get(key, 0) + 1
        cache.set(key, value, CREDENTIAL_COOLDOWN_SECONDS)
        attempts.append(value)
    if max(attempts) == MAX_CREDENTIAL_ATTEMPTS:
        AuditLog.record(
            action=AuditAction.PERMISSION,
            status=AuditStatus.FAILURE,
            actor=actor,
            target=target,
            target_type="User",
            target_label=str(account_key),
            message="凭据验证连续失败，账号或来源 IP 进入短时冷却。",
            metadata={"kind": kind, "attempts": max(attempts)},
            ip_address=ip_address,
        )
    return max(attempts)


class RequiredPasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path

        # Keep the old Wagtail URL as a redirect-only compatibility endpoint.
        if path == LEGACY_PASSWORD_CHANGE_PATH:
            if user and user.is_authenticated:
                return redirect("account:change_password")
            return redirect(f"/admin/login/?next={NEUTRAL_PASSWORD_CHANGE_PATH}")

        # This check must precede forced-password handling: a pure author must
        # receive a controlled 403 for /admin/, even when a password change is
        # still required.
        if (
            user
            and user.is_authenticated
            and getattr(user, "is_author", False)
            and not user.is_staff
            and any(path.startswith(prefix) for prefix in AUTHOR_FORBIDDEN_PREFIXES)
            and path not in {"/admin/login/", "/admin/logout/"}
        ):
            AuditLog.record(
                action=AuditAction.PERMISSION,
                status=AuditStatus.FAILURE,
                actor=user,
                target=user,
                message="纯作者账号访问编辑后台被拒绝。",
                metadata={
                    "operation_source": "author_admin_boundary",
                    "method": request.method,
                    "path": path,
                    "result": "denied",
                },
                ip_address=request_ip(request),
            )
            return render(request, "author/admin_forbidden.html", status=403)

        is_password_change_resource = (
            request.method in {"GET", "HEAD"}
            and request.path in PASSWORD_CHANGE_RESOURCE_PATHS
        )
        if (
            user
            and user.is_authenticated
            and user.must_change_password
            and path.startswith("/admin/")
            and path not in {"/admin/login/", "/admin/logout/"}
            and not is_password_change_resource
        ):
            return redirect("account:change_password")
        if (
            user
            and user.is_authenticated
            and user.must_change_password
            and path.startswith("/author/")
            and path not in {"/author/login/", "/author/logout/"}
        ):
            return redirect("account:change_password")

        response = self.get_response(request)
        if (
            path.rstrip("/") == "/admin/login"
            and response.status_code in {301, 302, 303}
            and getattr(request.user, "is_authenticated", False)
        ):
            set_login_scope(request, ADMIN_SCOPE)
        if path.rstrip("/") in {"/admin/logout", "/author/logout"}:
            clear_login_scope(request)
        return response


class CredentialRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_login = request.path.rstrip("/") in {"/admin/login", "/author/login"}
        if request.method != "POST" or not is_login:
            return self.get_response(request)
        username = str(request.POST.get("username", "")).strip().lower()
        ip_address = request_ip(request)
        if credential_is_limited("login", username, ip_address):
            return HttpResponse("登录尝试过多，请稍后再试。", status=429)
        response = self.get_response(request)
        if response.status_code in {301, 302, 303}:
            clear_credential_failures("login", username, ip_address)
            return response
        user = get_user_model().objects.filter(username__iexact=username).first()
        record_credential_failure(
            "login", username, ip_address, actor=user, target=user
        )
        return response
