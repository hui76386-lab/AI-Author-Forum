from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import Journal, JournalEditorAssignment
from ai_author_forum.test_helpers import (
    grant_business_super_admin,
)


class ArticleJournalContextAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.root_page = Page.get_first_root_node()
        cls.content_user = cls.user_model.objects.create_user(
            username="article-journal-content",
            email="article-journal-content@example.com",
            display_name="Article Journal Content",
            password="test-password",
            is_staff=True,
        )
        cls.readonly_article_user = cls.user_model.objects.create_user(
            username="article-journal-no-placement",
            email="article-journal-no-placement@example.com",
            display_name="Article Journal Readonly",
            password="test-password",
            is_staff=True,
        )
        cls.role_admin = grant_business_super_admin(
            cls.user_model.objects.create_superuser(
                username="article-journal-role-admin",
                email="article-journal-role-admin@example.com",
                password="test-password",
            )
        )
        cls.primary_journal = Journal.objects.create(
            name="AI Methods",
            name_cn="人工智能方法",
            slug="ai-methods",
            az_group="A",
            sort_order=10,
        )
        cls.other_journal = Journal.objects.create(
            name="Digital Scholarship",
            name_cn="数字学术",
            slug="digital-scholarship",
            az_group="D",
            sort_order=20,
        )
        appoint_journal_editor(
            actor=cls.role_admin,
            user=cls.content_user,
            journal=cls.primary_journal,
            role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            responsibilities=[
                JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
            ],
            public_profile={"public_name": cls.content_user.display_name},
        )
        appoint_journal_editor(
            actor=cls.role_admin,
            user=cls.readonly_article_user,
            journal=cls.primary_journal,
            role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            responsibilities=[JournalEditorAssignment.Responsibility.JOURNAL_PROFILE],
            public_profile={"public_name": cls.readonly_article_user.display_name},
        )
        cls.primary_article = cls.create_article(
            "Primary journal article",
            cls.primary_journal,
            cls.content_user,
        )
        cls.other_article = cls.create_article(
            "Related but not primary article",
            cls.other_journal,
            cls.content_user,
        )
        cls.other_article.related_journals.add(cls.primary_journal)

    @classmethod
    def create_article(cls, title, journal, owner):
        article = ArticlePage(
            title=title,
            slug=title.lower().replace(" ", "-"),
            abstract=f"{title} abstract",
            body=[("paragraph", f"<p>{title} body</p>")],
            authors="Test Author",
            keywords="cms, journal",
            primary_journal=journal,
            owner=owner,
        )
        cls.root_page.add_child(instance=article)
        return article

    def journal_list_url(self, journal_id):
        return f"{reverse('article_admin:index')}?primary_journal={journal_id}"

    def test_primary_journal_filter_preserves_main_object_context(self):
        self.client.force_login(self.content_user)

        response = self.client.get(self.journal_list_url(self.primary_journal.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [article.pk for article in response.context["articles"]],
            [self.primary_article.pk],
        )
        self.assertNotContains(response, self.other_article.title)
        self.assertEqual(response.context["journal_context"], self.primary_journal)
        self.assertContains(response, self.primary_journal.name_cn)
        self.assertContains(response, self.primary_journal.slug)
        self.assertContains(response, "返回子期刊工作台")
        self.assertContains(response, "审核通过后仍需进入投放管理")
        self.assertContains(response, "不代表这些文章已经投放到本刊前台")
        self.assertEqual(
            response.context["journal_workspace_url"],
            reverse("journals:workspace", args=[self.primary_journal.pk]),
        )
        self.assertEqual(
            response.context["journal_placements_url"],
            f"{reverse('placements:index')}?target=journal:{self.primary_journal.slug}",
        )

    def test_content_manager_gets_create_article_action(self):
        self.client.force_login(self.content_user)

        list_url = self.journal_list_url(self.primary_journal.pk)
        response = self.client.get(list_url)

        self.assertEqual(response.status_code, 200)
        create_url = response.context["create_article_url"]
        parsed_url = urlsplit(create_url)
        expected_path = reverse(
            "wagtailadmin_pages:add",
            args=[
                ArticlePage._meta.app_label,
                ArticlePage._meta.model_name,
                self.root_page.pk,
            ],
        )
        self.assertEqual(parsed_url.path, expected_path)
        self.assertEqual(parse_qs(parsed_url.query)["next"], [list_url])
        self.assertContains(response, "新建文章")
        self.assertEqual(self.client.get(create_url).status_code, 200)

    def test_user_without_article_edit_permission_has_no_create_action(self):
        self.client.force_login(self.readonly_article_user)

        response = self.client.get(self.journal_list_url(self.primary_journal.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["create_article_url"], "")
        self.assertNotContains(response, "新建文章")

    def test_missing_creation_parent_does_not_render_dead_link(self):
        self.client.force_login(self.content_user)

        with patch(
            "ai_author_forum.articles.views.Page.get_first_root_node",
            return_value=None,
        ):
            response = self.client.get(reverse("article_admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["create_article_url"], "")
        self.assertNotContains(response, "新建文章")

    def test_related_journal_does_not_change_primary_journal_filtering(self):
        self.client.force_login(self.content_user)

        response = self.client.get(self.journal_list_url(self.primary_journal.pk))

        article_ids = {article.pk for article in response.context["articles"]}
        self.assertIn(self.primary_article.pk, article_ids)
        self.assertNotIn(self.other_article.pk, article_ids)

    def test_invalid_journal_id_does_not_render_context_card(self):
        self.client.force_login(self.content_user)

        response = self.client.get(self.journal_list_url(999999))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["journal_context"])
        self.assertEqual(response.context["journal_workspace_url"], "")
        self.assertEqual(response.context["journal_placements_url"], "")
        self.assertNotContains(response, "正在处理子期刊")
        self.assertNotContains(response, "返回子期刊工作台")

    def test_user_without_placement_access_only_gets_workspace_return_link(self):
        self.client.force_login(self.readonly_article_user)

        response = self.client.get(self.journal_list_url(self.primary_journal.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["journal_context"], self.primary_journal)
        self.assertNotEqual(response.context["journal_workspace_url"], "")
        self.assertEqual(response.context["journal_placements_url"], "")
        self.assertContains(response, "返回子期刊工作台")
        self.assertNotContains(response, "管理本刊投放")
