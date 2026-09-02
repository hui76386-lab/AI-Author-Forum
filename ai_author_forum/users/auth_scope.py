"""Session-only login scope used for post-login password-change routing."""

from __future__ import annotations

from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

LOGIN_SCOPE_SESSION_KEY = "login_scope"
AUTHOR_SCOPE = "author"
ADMIN_SCOPE = "admin"
VALID_LOGIN_SCOPES = frozenset({AUTHOR_SCOPE, ADMIN_SCOPE})


def set_login_scope(request, scope: str) -> None:
    if scope in VALID_LOGIN_SCOPES:
        request.session[LOGIN_SCOPE_SESSION_KEY] = scope


def get_login_scope(request) -> str | None:
    scope = request.session.get(LOGIN_SCOPE_SESSION_KEY)
    return scope if scope in VALID_LOGIN_SCOPES else None


def clear_login_scope(request) -> None:
    request.session.pop(LOGIN_SCOPE_SESSION_KEY, None)


def safe_internal_next(request, value: str | None, *, scope: str) -> str:
    """Return an internal next path belonging to the selected login scope."""

    fallback = (
        reverse("author:dashboard")
        if scope == AUTHOR_SCOPE
        else reverse("wagtailadmin_home")
    )
    if not value or not url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return fallback
    path = value.split("?", 1)[0]
    if scope == AUTHOR_SCOPE and path.startswith("/admin/"):
        return fallback
    if scope == ADMIN_SCOPE and path.startswith("/author/"):
        return fallback
    return value
