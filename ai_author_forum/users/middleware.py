from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect

from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

MAX_CREDENTIAL_ATTEMPTS = 5
CREDENTIAL_COOLDOWN_SECONDS = 900
PASSWORD_CHANGE_RESOURCE_PATHS = frozenset(
    {
        "/admin/jsi18n/",
        "/admin/sprite/",
    }
)


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
        is_password_change_resource = (
            request.method in {"GET", "HEAD"}
            and request.path in PASSWORD_CHANGE_RESOURCE_PATHS
        )
        if (
            user
            and user.is_authenticated
            and user.must_change_password
            and request.path.startswith("/admin/")
            and not request.path.startswith("/admin/accounts/change-password/")
            and not request.path.startswith("/admin/logout/")
            and not is_password_change_resource
        ):
            return redirect("account_admin:change_password")
        return self.get_response(request)


class CredentialRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_login = request.path.rstrip("/") == "/admin/login"
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
