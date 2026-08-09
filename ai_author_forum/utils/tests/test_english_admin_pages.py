from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from wagtail.models import Page

from ai_author_forum.articles.models import (
    ArticleCategoryAssignment,
    ArticlePage,
)
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)


class EnglishAdminPageContractTests(TestCase):
    han_pattern = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
    han_run_pattern = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="english-admin",
            email="english-admin@example.com",
            password="test-password",
        )
        grant_business_super_admin(cls.user)
        cls.root_page = Page.get_first_root_node()
        cls.journal = Journal.objects.create(
            name="English Journal",
            name_cn="\u4e2d\u6587\u671f\u520a\u540d",
            slug="english-journal",
            az_group="E",
        )
        article = ArticlePage(
            title="English dynamic article title",
            slug="english-contract-article",
            abstract="English abstract",
            body=[("paragraph", "<p>English body</p>")],
            authors="English author",
            keywords="english, contract",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            primary_journal=cls.journal,
            owner=cls.user,
        )
        cls.root_page.add_child(instance=article)
        category = JournalCategory.objects.create(
            journal=cls.journal,
            code="DYNAMIC",
            name="Dynamic category",
            slug="dynamic",
            depth=1,
            path_cache="dynamic",
        )
        ArticleCategoryAssignment.objects.create(
            article=article, category=category, is_primary=True
        )
        formally_approve_test_article(article, actor=cls.user)
        ArticlePage.objects.filter(pk=article.pk).update(
            publication_status=ArticlePage.PublicationStatus.PUBLISHED
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"

    def assert_english_html(self, response, expected_text):
        self.assertEqual(response.status_code, 200)
        body = response.content.decode(response.charset or "utf-8")
        self.assertIn(expected_text, body)
        matches = list(self.han_run_pattern.finditer(body))
        match = matches[0] if matches else None
        contexts = sorted(
            {body[max(0, item.start() - 80) : item.end() + 100] for item in matches}
        )
        self.assertIsNone(
            match,
            "English response leaked Han text: "
            f"{sorted(set(self.han_run_pattern.findall(body)))[:200]!r}; "
            f"contexts={contexts[:20]!r}",
        )

    def test_priority_admin_pages_render_english_only(self):
        article_add_url = reverse(
            "wagtailadmin_pages:add",
            args=[
                ArticlePage._meta.app_label,
                ArticlePage._meta.model_name,
                self.root_page.pk,
            ],
        )
        pages = (
            (reverse("wagtailadmin_home"), "Business workspace"),
            (reverse("article_admin:index"), "All articles"),
            (reverse("journals_import_dashboard"), "Journal import center"),
            (reverse("article_admin:import"), "Article batch import center"),
            (reverse("homepage-composition:index"), "Homepage composition"),
            (reverse("placements:index"), "Placement overview"),
            (reverse("placements:new_single"), "Select article"),
            (reverse("placements:journals"), "Journal curation"),
            (reverse("placements:bulk_new"), "Bulk placement"),
            (reverse("placements:list"), "Placement list"),
            (reverse("placements:batches"), "Placement batches"),
            (article_add_url, "New: Article page"),
            (
                reverse("wagtailsnippetchoosers_journals_journal:choose"),
                "English Journal",
            ),
        )
        for url, expected_text in pages:
            with self.subTest(url=url):
                response = self.client.get(
                    url,
                    follow=url
                    in {
                        reverse("placements:new_single"),
                        reverse("placements:bulk_new"),
                    },
                )
                self.assert_english_html(response, expected_text)
                body = response.content.decode(response.charset or "utf-8")
                self.assertNotIn("Content unavailable in English", body)
                if url == reverse("wagtailadmin_home"):
                    self.assertIn("Article management", body)
                    self.assertIn("Static publishing", body)
                    self.assertIn("Account management", body)
                    self.assertIn("Editorial team", body)
                    self.assertNotIn("English text", body)
                elif url == reverse("article_admin:index"):
                    self.assertIn("AI Article", body)
                    self.assertIn("Approved", body)
                    self.assertIn("Static version published", body)
                    self.assertIn('placeholder="YYYY-MM-DD"', body)
                elif url == reverse("journals_import_dashboard"):
                    self.assertIn("Import package", body)
                    self.assertIn("CSV encoding strategy", body)
                    self.assertNotIn("English text", body)
                elif url == reverse("article_admin:import"):
                    self.assertIn("Article import file", body)
                    self.assertIn("Default journal", body)
                    self.assertNotIn("English text", body)
                elif url == reverse("homepage-composition:index"):
                    self.assertNotIn("English text", body)
                elif url == reverse("placements:index"):
                    self.assertNotIn("Overview Overview", body)
                    self.assertNotIn("PlacementOverview", body)
                    self.assertNotIn("Recent batches / Recent batches", body)
                    self.assertNotIn("Needs attention / Needs attention", body)

    def test_journal_chooser_modal_payload_is_english(self):
        response = self.client.get(
            reverse("wagtailsnippetchoosers_journals_journal:choose")
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        body = payload["html"]
        self.assertIn("Select", body)
        self.assertIn(">journal</span>", body)
        self.assertIn("English Journal", body)
        self.assertNotIn("Content unavailable in English", body)
        self.assertIsNone(self.han_pattern.search(body))

    def test_journal_string_uses_the_active_admin_language(self):
        with translation.override("en"):
            self.assertEqual(str(self.journal), "English Journal")
        with translation.override("zh-hans"):
            self.assertEqual(str(self.journal), "中文期刊名")
