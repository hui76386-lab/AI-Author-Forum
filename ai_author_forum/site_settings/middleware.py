from __future__ import annotations

import json

from django.middleware.locale import LocaleMiddleware
from django.utils import translation


class AdminNavigationPreviewFrameOptionsMiddleware:
    """Allow authenticated staff to embed explicit front-end previews in admin."""

    preview_parameter = "admin_navigation_preview"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_preview = request.GET.get(self.preview_parameter) == "1"
        is_frontend_path = request.path == "/" or request.path.startswith("/journals/")
        user = getattr(request, "user", None)
        if (
            is_preview
            and is_frontend_path
            and user is not None
            and user.is_authenticated
            and user.is_staff
        ):
            response["X-Frame-Options"] = "SAMEORIGIN"
        return response


class AdminLocaleMiddleware(LocaleMiddleware):
    """Honor the language cookie for unprefixed Wagtail admin URLs."""

    admin_prefixes = ("/admin/", "/django-admin/", "/documents/")

    def process_request(self, request):
        if request.path_info.startswith(self.admin_prefixes):
            language = translation.get_language_from_request(request, check_path=False)
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()
            return None
        return super().process_request(request)


class EnglishAdminResponseMiddleware:
    """Translate legacy labels on English admin HTML responses."""

    admin_prefixes = ("/admin/", "/django-admin/", "/documents/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        english_surface = request.path_info.startswith(self.admin_prefixes)
        if not english_surface:
            return response
        if not (translation.get_language() or "").lower().startswith("en"):
            return response
        content_type = response.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {"text/html", "application/json"}:
            return response
        from ai_author_forum.utils.admin_ui import sanitize_english_admin_html

        charset = response.charset or "utf-8"
        if content_type == "text/html":
            response.content = sanitize_english_admin_html(
                response.content.decode(charset)
            ).encode(charset)
        else:
            payload = json.loads(response.content.decode(charset))
            # Wagtail chooser responses wrap modal markup in a structured JSON
            # envelope.  Translate only that presentation field so API values
            # and chooser identifiers remain byte-for-byte business data.
            if not isinstance(payload, dict) or not isinstance(
                payload.get("html"), str
            ):
                return response
            payload["html"] = sanitize_english_admin_html(payload["html"])
            response.content = json.dumps(payload, ensure_ascii=False).encode(charset)
        # CommonMiddleware may have calculated this before the English-only
        # rewrite. Leave framing to the server so HTTP/2 does not receive a
        # stale byte count and abort the response.
        response.headers.pop("Content-Length", None)
        return response
