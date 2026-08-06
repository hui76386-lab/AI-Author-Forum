from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
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

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="english-admin",
            email="english-admin@example.com",
            password="test-password",
        )
        grant_business_super_admin(cls.user)
        journal = Journal.objects.create(
            name="English Journal",
            name_cn="\u4e2d\u6587\u671f\u520a\u540d",
            slug="english-journal",
            az_group="E",
        )
        article = ArticlePage(
            title="\u4e2d\u6587\u52a8\u6001\u6587\u7ae0\u6807\u9898",
            slug="english-contract-article",
            abstract="\u4e2d\u6587\u6458\u8981",
            body=[("paragraph", "<p>English body</p>")],
            authors="\u4e2d\u6587\u4f5c\u8005",
            keywords="english, contract",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            primary_journal=journal,
            owner=cls.user,
        )
        Page.get_first_root_node().add_child(instance=article)
        category = JournalCategory.objects.create(
            journal=journal,
            code="DYNAMIC",
            name="\u4e2d\u6587\u52a8\u6001\u680f\u76ee",
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
        match = self.han_pattern.search(body)
        self.assertIsNone(
            match,
            f"English response leaked Han text near {body[max(0, match.start() - 40):match.start() + 80] if match else ''!r}",
        )

    def test_priority_admin_pages_render_english_only(self):
        pages = (
            (reverse("wagtailadmin_home"), "Business workspace"),
            (reverse("article_admin:index"), "All articles"),
            (reverse("journals_import_dashboard"), "Journal import center"),
            (reverse("article_admin:import"), "Article batch import center"),
            (reverse("homepage-composition:index"), "Homepage composition"),
        )
        for url, expected_text in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assert_english_html(response, expected_text)
                body = response.content.decode(response.charset or "utf-8")
                if url == reverse("wagtailadmin_home"):
                    self.assertIn("Article management", body)
                    self.assertIn("Static publishing", body)
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
