from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.editor_services import (
    appoint_journal_editor,
    update_journal_profile,
)
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)


class JournalAdminWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.superuser = cls.user_model.objects.create_superuser(
            username="journal-workspace-admin",
            password="test-password",
            email="admin@example.com",
        )
        grant_business_super_admin(cls.superuser)
        cls.journal = Journal.objects.create(
            name="AI Research",
            name_cn="人工智能研究",
            slug="ai-research",
            az_group="A",
            status="active",
        )
        cls.other_journal = Journal.objects.create(
            name="Publishing Studies",
            name_cn="出版研究",
            slug="publishing-studies",
            az_group="P",
            status="active",
        )
        JournalCategory.objects.create(
            journal=cls.journal,
            name="Research",
            code="research",
            slug="research",
        )
        cls.primary_approved = cls.create_article(
            title="Approved primary article",
            slug="approved-primary-article",
            journal=cls.journal,
            approved=True,
        )
        cls.create_article(
            title="Draft primary article",
            slug="draft-primary-article",
            journal=cls.journal,
            approved=False,
        )
        related = cls.create_article(
            title="Related article",
            slug="related-article",
            journal=cls.other_journal,
            approved=True,
        )
        related.related_journals.add(cls.journal)
        slot = LayoutSlot.objects.create(
            title="Journal feature",
            code="journal-workspace-feature",
            scope=LayoutSlot.Scope.JOURNAL,
        )
        ArticlePlacement.objects.create(
            slot=slot,
            article=cls.primary_approved,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=cls.journal.slug,
        )
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            version="workspace-v1",
        )
        StaticManifest.objects.create(
            version="workspace-v1",
            job=job,
            is_active=True,
            metadata={
                "targets": [
                    {
                        "output_path": "journals/ai-research/index.html",
                        "status": "generated",
                        "action": "upsert",
                    }
                ]
            },
            files=[],
        )

    @classmethod
    def create_article(cls, *, title, slug, journal, approved):
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=journal,
        )
        Page.get_first_root_node().add_child(instance=article)
        if approved:
            formally_approve_test_article(article, actor=cls.superuser)
        return article

    def make_role_user(self, role_code):
        username = f"workspace-{role_code}"
        user = self.user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username,
            password="test-password",
            is_staff=True,
        )
        if role_code == "associate_editor":
            appoint_journal_editor(
                actor=self.superuser,
                user=user,
                journal=self.journal,
                role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
                responsibilities=[
                    JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
                ],
                public_profile={
                    "public_name": user.display_name,
                    "public_role_label": "副编辑",
                },
            )
        return user

    def test_user_without_journal_access_cannot_enter_admin_module(self):
        self.client.force_login(self.make_role_user("unassigned"))

        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )

        self.assertRedirects(
            response,
            f"/admin/login/?next={reverse('journals:workspace', args=[self.journal.pk])}",
            fetch_redirect_response=False,
        )

    def test_workspace_shows_master_object_metrics_and_relationship_rule(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["primary_article_count"], 2)
        self.assertEqual(response.context["approved_primary_article_count"], 1)
        self.assertEqual(response.context["related_article_count"], 1)
        self.assertEqual(response.context["category_count"], 1)
        self.assertEqual(response.context["current_placement_count"], 1)
        self.assertContains(response, "主属/相关期刊")
        self.assertContains(response, "ArticlePlacement")
        self.assertContains(response, "审核通过后不会自动出现在本刊前台")

    def test_workspace_links_keep_current_journal_context(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )

        self.assertContains(
            response,
            f"{reverse('article_admin:index')}?primary_journal={self.journal.pk}",
        )
        self.assertContains(
            response,
            f"{reverse('journals_category_admin')}?journal={self.journal.pk}",
        )
        self.assertContains(
            response,
            f"{reverse('placements:index')}?target=journal:{self.journal.slug}",
        )
        self.assertContains(
            response,
            f"{reverse('static_publish:center')}?journal={self.journal.slug}",
        )

    def test_create_journal_starts_as_draft_and_redirects_to_workspace(self):
        self.client.force_login(self.superuser)

        response = self.client.post(
            reverse("journals:new"),
            {
                "name": "Continuous Publishing",
                "name_cn": "连续出版测试",
                "slug": "continuous-publishing",
                "az_group": "C",
                "sort_order": 10,
            },
        )

        journal = Journal.objects.get(slug="continuous-publishing")
        self.assertEqual(journal.status, JournalStatus.DRAFT)
        self.assertRedirects(
            response,
            reverse("journals:workspace", args=[journal.pk]),
        )
        self.assertTrue(
            JournalCategory.objects.filter(
                journal=journal,
                code="MAIN",
                status="active",
            ).exists()
        )

    def test_create_form_hides_status_and_explains_next_step(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("journals:new"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("status", response.context["form"].fields)
        self.assertContains(response, "创建并进入工作台")
        self.assertContains(response, "新建记录会先保存为“草稿”")

    def test_prepared_chief_advances_draft_journal_to_activation_step(self):
        self.client.force_login(self.superuser)
        journal = Journal.objects.create(
            name="Draft Workflow Journal",
            slug="draft-workflow-journal",
            az_group="D",
            status=JournalStatus.DRAFT,
        )
        JournalCategory.objects.create(
            journal=journal,
            name="Main",
            code="MAIN-DRAFT",
            slug="main",
        )
        chief = self.user_model.objects.create_user(
            username="draft-workflow-chief",
            email="draft-workflow-chief@example.com",
            display_name="Draft Workflow Chief",
            is_staff=True,
        )
        JournalEditorAssignment.objects.create(
            journal=journal,
            user=chief,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            responsibilities=list(JournalEditorAssignment.ALL_RESPONSIBILITIES),
            public_name=chief.display_name,
            public_role_label="主编",
            created_by=self.superuser,
        )

        response = self.client.get(
            reverse("journals:workspace", args=[journal.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["chief_editor_count"], 1)
        self.assertEqual(response.context["next_step"]["title"], "启用子期刊")
        self.assertContains(response, "启用子期刊")

    def test_draft_journal_requires_one_prepared_chief_before_activation(self):
        journal = Journal.objects.create(
            name="Activation Guard Journal",
            slug="activation-guard-journal",
            az_group="A",
            status=JournalStatus.DRAFT,
        )

        with self.assertRaisesMessage(ValidationError, "启用前必须准备"):
            update_journal_profile(
                actor=self.superuser,
                journal=journal,
                values={"status": JournalStatus.ACTIVE},
            )
        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalStatus.DRAFT)

        chief = self.user_model.objects.create_user(
            username="activation-guard-chief",
            email="activation-guard-chief@example.com",
            display_name="Activation Guard Chief",
            is_staff=True,
        )
        JournalEditorAssignment.objects.create(
            journal=journal,
            user=chief,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            responsibilities=list(JournalEditorAssignment.ALL_RESPONSIBILITIES),
            public_name=chief.display_name,
            public_role_label="主编",
            created_by=self.superuser,
        )

        update_journal_profile(
            actor=self.superuser,
            journal=journal,
            values={"status": JournalStatus.ACTIVE},
        )
        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalStatus.ACTIVE)

    def test_associate_workspace_hides_global_only_actions(self):
        self.client.force_login(self.make_role_user("associate_editor"))

        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions"]["static_publish"], "")
        self.assertNotContains(
            response,
            f"{reverse('journals_category_admin')}?journal={self.journal.pk}",
        )

    def test_workspace_reports_active_static_manifest(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )

        self.assertContains(response, "已在活动 manifest 中")
        self.assertContains(response, "workspace-v1")
        self.assertContains(response, "/journals/ai-research/")

    def test_journal_list_title_opens_workspace_and_edit_stays_secondary(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("journals:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("journals:workspace", args=[self.journal.pk]),
        )
        self.assertContains(
            response,
            reverse("journals_profile", args=[self.journal.pk]),
        )

    def test_removed_site_operator_name_grants_no_authority(self):
        self.client.force_login(self.make_role_user("site_operator"))

        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )

        self.assertRedirects(
            response,
            f"/admin/login/?next={reverse('journals:workspace', args=[self.journal.pk])}",
            fetch_redirect_response=False,
        )
