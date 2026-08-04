from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase
from django.utils import translation

from ai_author_forum.site_settings.middleware import EnglishAdminResponseMiddleware
from ai_author_forum.utils.admin_i18n import admin_text
from ai_author_forum.utils.admin_ui import sanitize_english_admin_html
from ai_author_forum.utils.i18n import (
    DEFAULT_LANGUAGE,
    ENGLISH_LANGUAGE,
    UI_TRANSLATIONS,
    language_switch_options,
    localize_path,
    localized_output_path,
    strip_public_language_prefix,
    ui_label,
)


class FrontendI18nUtilityTests(SimpleTestCase):
    def test_public_paths_are_localized_without_prefixing_default_language(self):
        self.assertEqual(localize_path("/journals/", DEFAULT_LANGUAGE), "/journals/")
        self.assertEqual(localize_path("/journals/", ENGLISH_LANGUAGE), "/en/journals/")
        self.assertEqual(localize_path("/en/journals/", DEFAULT_LANGUAGE), "/journals/")
        self.assertEqual(
            localize_path("/journals/?q=ai", ENGLISH_LANGUAGE),
            "/en/journals/?q=ai",
        )
        self.assertEqual(
            strip_public_language_prefix("/en/articles/example/"),
            "/articles/example/",
        )

    def test_static_output_paths_are_converted_to_localized_public_urls(self):
        self.assertEqual(localized_output_path("index.html", DEFAULT_LANGUAGE), "/")
        self.assertEqual(localized_output_path("index.html", ENGLISH_LANGUAGE), "/en/")
        self.assertEqual(
            localized_output_path("journals/example/index.html", ENGLISH_LANGUAGE),
            "/en/journals/example/",
        )

    def test_language_switch_options_preserve_query_strings(self):
        with translation.override(ENGLISH_LANGUAGE):
            options = language_switch_options("/en/search/", "q=ai&page=2")
        by_code = {option["code"]: option for option in options}

        self.assertEqual(by_code[DEFAULT_LANGUAGE]["url"], "/search/?q=ai&page=2")
        self.assertEqual(by_code[ENGLISH_LANGUAGE]["url"], "/en/search/?q=ai&page=2")
        self.assertTrue(by_code[ENGLISH_LANGUAGE]["active"])

    def test_ui_text_is_available_for_all_template_keys_in_both_languages(self):
        template_dir = Path(settings.BASE_DIR) / "templates"
        keys = set()
        for template_path in template_dir.rglob("*.html"):
            template_text = template_path.read_text(encoding="utf-8")
            keys.update(
                match.group(1)
                for match in re.finditer(
                    r"ui_text\s+['\"]([^'\"]+)['\"]", template_text
                )
            )

        missing_default = sorted(keys - set(UI_TRANSLATIONS[DEFAULT_LANGUAGE]))
        missing_english = sorted(keys - set(UI_TRANSLATIONS[ENGLISH_LANGUAGE]))
        self.assertEqual(missing_default, [])
        self.assertEqual(missing_english, [])

    def test_ui_label_uses_active_language_and_default_fallback(self):
        with translation.override(DEFAULT_LANGUAGE):
            self.assertEqual(ui_label("search"), "\u641c\u7d22")
        with translation.override(ENGLISH_LANGUAGE):
            self.assertEqual(ui_label("search"), "Search")
            self.assertEqual(ui_label("missing-key", default="Fallback"), "Fallback")


class AdminI18nUtilityTests(SimpleTestCase):
    def test_admin_text_uses_active_language(self):
        with translation.override(DEFAULT_LANGUAGE):
            self.assertEqual(
                str(admin_text("articles.manage")), "\u6587\u7ae0\u7ba1\u7406"
            )
        with translation.override(ENGLISH_LANGUAGE):
            self.assertEqual(str(admin_text("articles.manage")), "Article management")


class EnglishResponseSanitizerTests(SimpleTestCase):
    han_pattern = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

    def setUp(self):
        self.factory = RequestFactory()

    def test_known_and_unknown_han_text_is_removed_in_english(self):
        content = (
            "<p>AI \u6587\u7ae0 / \u672a\u6536\u5f55\u7684\u52a8\u6001\u6587\u6848</p>"
        )
        with translation.override(ENGLISH_LANGUAGE):
            sanitized = sanitize_english_admin_html(content)

        self.assertIn("AI Article", sanitized)
        self.assertIn("Content unavailable in English", sanitized)
        self.assertIsNone(self.han_pattern.search(sanitized))

    def test_chinese_response_is_not_rewritten(self):
        content = "<p>AI \u6587\u7ae0</p>"
        with translation.override(DEFAULT_LANGUAGE):
            self.assertEqual(sanitize_english_admin_html(content), content)

    def test_middleware_covers_admin_and_english_public_html_only(self):
        def html_response(_request):
            response = HttpResponse("<p>AI \u6587\u7ae0</p>", content_type="text/html")
            response["Content-Length"] = str(len(response.content))
            return response

        middleware = EnglishAdminResponseMiddleware(html_response)
        for path in ("/admin/articles/", "/en/articles/example/"):
            with self.subTest(path=path), translation.override(ENGLISH_LANGUAGE):
                response = middleware(self.factory.get(path))
                body = response.content.decode(response.charset)
                self.assertIn("AI Article", body)
                self.assertIsNone(self.han_pattern.search(body))
                self.assertNotIn("Content-Length", response)

        with translation.override(DEFAULT_LANGUAGE):
            response = middleware(self.factory.get("/admin/articles/"))
            self.assertContains(response, "AI \u6587\u7ae0")

        json_middleware = EnglishAdminResponseMiddleware(
            lambda _request: JsonResponse({"label": "AI \u6587\u7ae0"})
        )
        with translation.override(ENGLISH_LANGUAGE):
            response = json_middleware(self.factory.get("/admin/articles/status/"))
            self.assertIn("\\u6587\\u7ae0", response.content.decode(response.charset))
