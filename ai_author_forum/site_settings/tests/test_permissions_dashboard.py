from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from ai_author_forum.journals.editor_services import sync_editor_access_group
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.dashboard import get_role_dashboard_context
from ai_author_forum.site_settings.middleware import (
    AdminNavigationPreviewFrameOptionsMiddleware,
)
from ai_author_forum.site_settings.permissions import get_admin_permission_context
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


class SimpleRoleDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.admin = cls.user_model.objects.create_user(
            username="dashboard-admin",
            email="dashboard-admin@example.com",
            display_name="Dashboard Admin",
            password="test-password",
            is_staff=True,
        )
        cls.admin.groups.add(
            cls.admin.groups.model.objects.get(name=SUPER_ADMIN_GROUP_NAME)
        )
        cls.journal_a = Journal.objects.create(
            name="Journal A",
            slug="dashboard-journal-a",
            status=JournalStatus.ACTIVE,
            az_group="D",
        )
        cls.journal_b = Journal.objects.create(
            name="Journal B",
            slug="dashboard-journal-b",
            status=JournalStatus.ACTIVE,
            az_group="D",
        )

    def make_editor(
        self,
        username,
        role,
        responsibilities=(),
        *,
        journal=None,
        starts_at=None,
        ends_at=None,
    ):
        user = self.user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username,
            password="test-password",
            is_staff=True,
        )
        assignment = JournalEditorAssignment.objects.create(
            user=user,
            journal=journal or self.journal_a,
            role=role,
            responsibilities=list(responsibilities),
            public_name=username,
            public_role_label=JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role],
            created_by=self.admin,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        sync_editor_access_group(user)
        return user, assignment

    def test_role_permission_matrix(self):
        chief, _ = self.make_editor("chief", JournalEditorAssignment.Role.CHIEF_EDITOR)
        executive, _ = self.make_editor(
            "executive", JournalEditorAssignment.Role.EXECUTIVE_EDITOR
        )
        article_associate, _ = self.make_editor(
            "article-associate",
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            (JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,),
        )
        issue_associate, _ = self.make_editor(
            "issue-associate",
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            (JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT,),
        )

        admin_flags = get_admin_permission_context(self.admin)
        chief_flags = get_admin_permission_context(chief)
        executive_flags = get_admin_permission_context(executive)
        article_flags = get_admin_permission_context(article_associate)
        issue_flags = get_admin_permission_context(issue_associate)

        self.assertTrue(admin_flags["can_publish_static"])
        self.assertTrue(chief_flags["can_manage_placement"])
        self.assertTrue(executive_flags["can_manage_placement"])
        self.assertTrue(article_flags["can_manage_placement"])
        self.assertTrue(chief_flags["can_add_placement"])
        self.assertFalse(executive_flags["can_add_placement"])
        self.assertFalse(article_flags["can_add_placement"])
        self.assertTrue(article_flags["can_edit_article"])
        self.assertFalse(issue_flags["can_edit_article"])
        self.assertTrue(issue_flags["can_manage_issues"])
        self.assertFalse(issue_flags["can_manage_journal_categories"])
        for flags in (chief_flags, executive_flags, article_flags, issue_flags):
            self.assertFalse(flags["can_publish_static"])
            self.assertTrue(flags["can_view_audit_log"])

    def test_associate_workspace_only_contains_assigned_responsibility_entries(self):
        user, _ = self.make_editor(
            "column-associate",
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            (JournalEditorAssignment.Responsibility.COLUMN_NAVIGATION,),
        )
        context = get_role_dashboard_context(user)
        links = {
            link["label"]
            for card in context["workspace_cards"]
            for link in card["links"]
        }
        self.assertIn("栏目管理", links)
        self.assertIn("期刊导航", links)
        self.assertNotIn("期刊资料", links)
        self.assertNotIn("期次管理", links)
        self.assertNotIn("图片与素材", links)
        self.assertNotIn("投放管理", links)
        self.assertNotIn("静态发布中心", links)

    def test_chief_dashboard_has_final_review_and_editor_load_metrics(self):
        chief, _ = self.make_editor(
            "metrics-chief", JournalEditorAssignment.Role.CHIEF_EDITOR
        )
        context = get_role_dashboard_context(chief)
        role_section = next(
            section
            for section in context["dashboard_sections"]
            if section["code"] == "editor-role"
        )
        labels = {metric["label"] for metric in role_section["metrics"]}
        self.assertTrue(
            {"本刊待初审", "本刊待终审", "编辑负载", "未设置职责的副编辑"}.issubset(
                labels
            )
        )

    def test_expired_assignment_removes_dashboard_access_immediately(self):
        user, _ = self.make_editor(
            "expired-editor",
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            (JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,),
            ends_at=timezone.now() - timedelta(minutes=1),
        )
        flags = get_admin_permission_context(user)
        self.assertFalse(flags["has_dashboard_access"])
        self.assertFalse(flags["can_review_article"])

    def test_editor_dashboard_urls_are_admin_urls(self):
        user, _ = self.make_editor(
            "url-executive", JournalEditorAssignment.Role.EXECUTIVE_EDITOR
        )
        context = get_role_dashboard_context(user)
        urls = [
            item["url"]
            for section in context["dashboard_sections"]
            for item in section["metrics"]
        ] + [
            link["url"] for card in context["workspace_cards"] for link in card["links"]
        ]
        self.assertTrue(urls)
        self.assertTrue(all(url.startswith("/admin/") for url in urls))
        self.assertIn(reverse("journals_publication_issue_admin"), urls)


class AdminNavigationPreviewFrameOptionsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = AdminNavigationPreviewFrameOptionsMiddleware(
            lambda request: HttpResponse("preview")
        )

    def test_staff_preview_is_same_origin_frameable(self):
        request = self.factory.get("/?admin_navigation_preview=1")
        request.user = get_user_model().objects.create_user(
            username="preview-staff",
            email="preview-staff@example.com",
            display_name="Preview Staff",
            password="test-password",
            is_staff=True,
        )
        response = self.middleware(request)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_unmarked_request_does_not_override_frame_policy(self):
        request = self.factory.get("/")
        request.user = get_user_model().objects.create_user(
            username="preview-unmarked",
            email="preview-unmarked@example.com",
            display_name="Preview Unmarked",
            password="test-password",
            is_staff=True,
        )
        response = self.middleware(request)
        self.assertNotIn("X-Frame-Options", response)
