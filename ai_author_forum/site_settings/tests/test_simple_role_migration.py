import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.role_migration import build_report, validate_mapping
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from ..models import AuditLog


class SimpleRoleMigrationAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        user_model = get_user_model()
        cls.admin = user_model.objects.create_user(
            username="migration-admin",
            email="migration-admin@example.com",
            display_name="Migration Admin",
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.chief = user_model.objects.create_user(
            username="migration-chief",
            email="migration-chief@example.com",
            display_name="Migration Chief",
            is_staff=True,
        )
        cls.legacy_lead = user_model.objects.create_user(
            username="legacy-project-lead",
            email="legacy-project-lead@example.com",
            display_name="Legacy Project Lead",
            is_staff=True,
        )
        cls.journal = Journal.objects.create(
            name="Migration Journal",
            slug="migration-journal",
            status=JournalStatus.ACTIVE,
            az_group="M",
        )
        legacy_group = Group.objects.create(name="项目总负责人")
        cls.legacy_lead.groups.add(legacy_group)
        review_permission = Permission.objects.get(
            content_type__app_label="articles",
            codename="review_article",
        )
        legacy_group.permissions.add(review_permission)
        cls.legacy_lead.user_permissions.add(review_permission)

        article = ArticlePage(
            title="Legacy approved without final source",
            slug="legacy-approved-without-final-source",
            static_slug="legacy-approved-without-final-source",
            abstract="Legacy review state",
            body=[("paragraph", "<p>Legacy review state</p>")],
            authors="Legacy Author",
            keywords="legacy",
            responsibility_statement="Legacy authors retain responsibility.",
            article_type=ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            primary_journal=cls.journal,
        )
        Page.get_first_root_node().add_child(instance=article)
        revision = article.save_revision(
            user=cls.legacy_lead,
            bypass_article_permission_check=True,
        )
        ArticlePage.objects.filter(pk=article.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED,
            approved_version=revision,
        )
        cls.legacy_article_id = article.pk

    def mapping(self):
        return {
            "super_admins": [self.admin.email],
            "assignments": [
                {
                    "user": self.chief.email,
                    "journal": self.journal.slug,
                    "role": JournalEditorAssignment.Role.CHIEF_EDITOR,
                    "responsibilities": [],
                    "public_name": self.chief.display_name,
                    "public_affiliation": "Migration Institute",
                    "public_role_label": "主编",
                    "display_order": 10,
                    "show_publicly": True,
                }
            ],
            "deactivate_users": [],
        }

    def write_mapping(self, directory, mapping=None):
        path = Path(directory, "mapping.json")
        path.write_text(
            json.dumps(mapping or self.mapping(), ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_report_lists_legacy_bypasses_and_unverified_approved_article(self):
        report = build_report(self.mapping())
        self.assertFalse(report["policy"]["automatic_all_journal_grants"])
        self.assertIn(
            self.legacy_lead.username,
            report["legacy_project_lead_members"],
        )
        self.assertIn(
            self.legacy_lead.username,
            report["direct_review_permission_users"],
        )
        legacy_group = next(
            row for row in report["legacy_groups"] if row["name"] == "项目总负责人"
        )
        self.assertIn("articles.review_article", legacy_group["permissions"])
        self.assertEqual(legacy_group["permission_count"], 1)
        self.assertEqual(legacy_group["page_permissions"], [])
        self.assertEqual(legacy_group["collection_permissions"], [])
        self.assertEqual(
            [row["pk"] for row in report["legacy_approved_without_final_source"]],
            [self.legacy_article_id],
        )
        self.assertEqual(report["mapping_validation"]["errors"], [])

    def test_empty_legacy_project_lead_group_does_not_report_null_member(self):
        self.legacy_lead.groups.remove(Group.objects.get(name="项目总负责人"))

        report = build_report(self.mapping())

        self.assertEqual(report["legacy_project_lead_members"], [])

    def test_empty_mapping_never_infers_scope_and_blocks_missing_chief(self):
        validation = validate_mapping(
            {"super_admins": [], "assignments": [], "deactivate_users": []}
        )
        self.assertIn(
            "No journal assignments were supplied; no journal scope is inferred.",
            validation["warnings"],
        )
        self.assertTrue(
            any("exactly one chief editor" in error for error in validation["errors"])
        )
        self.assertEqual(JournalEditorAssignment.objects.count(), 0)

    def test_dry_run_is_read_only(self):
        with TemporaryDirectory() as directory:
            mapping_path = self.write_mapping(directory)
            output = StringIO()
            call_command(
                "apply_simple_role_migration",
                str(mapping_path),
                actor=self.admin.username,
                stdout=output,
            )
        self.assertIn("Dry-run only; no changes were written.", output.getvalue())
        self.assertEqual(JournalEditorAssignment.objects.count(), 0)
        self.assertTrue(Group.objects.get(name="项目总负责人").permissions.exists())

    def test_apply_is_idempotent_and_removes_legacy_business_authority(self):
        with TemporaryDirectory() as directory:
            mapping_path = self.write_mapping(directory)
            for _attempt in range(2):
                call_command(
                    "apply_simple_role_migration",
                    str(mapping_path),
                    actor=self.admin.username,
                    apply=True,
                    stdout=StringIO(),
                )

        assignments = JournalEditorAssignment.objects.filter(
            user=self.chief,
            journal=self.journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            is_active=True,
        )
        self.assertEqual(assignments.count(), 1)
        self.assertFalse(Group.objects.get(name="项目总负责人").permissions.exists())
        self.assertFalse(self.legacy_lead.user_permissions.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="SimpleRoleMigration",
                message="应用简化账号与子期刊角色迁移。",
            ).exists()
        )
