from __future__ import annotations

import csv
import io
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_author_forum.articles.import_permissions import (
    can_import_articles,
    can_override_suspicious_article_text,
)
from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    confirm_article_import,
    preview_article_import,
)
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.test_helpers import (
    grant_business_super_admin,
)


def csv_upload(*, title="Article", slug="article", suspicious=False):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "journal_slug",
            "title",
            "slug",
            "article_type",
            "authors",
            "body_html",
        ],
    )
    writer.writeheader()
    writer.writerow(
        {
            "journal_slug": "permission-journal",
            "title": "Broken ??? title" if suspicious else title,
            "slug": slug,
            "article_type": "news",
            "authors": "Author",
            "body_html": "<p>Body</p>",
        }
    )
    return SimpleUploadedFile(
        "articles.csv", stream.getvalue().encode("utf-8-sig"), content_type="text/csv"
    )


class ArticleImportPermissionMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.journal = Journal.objects.create(
            name="Permission Journal",
            slug="permission-journal",
            az_group="P",
            status=JournalStatus.ACTIVE,
        )
        cls.other_journal = Journal.objects.create(
            name="Permission Journal Two",
            slug="permission-journal-two",
            az_group="P",
            status=JournalStatus.ACTIVE,
        )
        cls.role_admin = grant_business_super_admin(
            get_user_model().objects.create_superuser(
                username="article-import-role-admin",
                email="article-import-role-admin@example.com",
                password="test-password",
            )
        )

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tempdir.cleanup)

    def make_role_user(self, role_code, suffix=""):
        username = (
            f"article-import-{role_code}-"
            f"{suffix or get_user_model().objects.count()}"
        )
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username,
            password="test-password",
            is_staff=True,
        )
        if role_code == "super_admin":
            grant_business_super_admin(user)
        elif role_code in {
            "chief_editor",
            "executive_editor",
            "associate_editor",
        }:
            appoint_journal_editor(
                actor=self.role_admin,
                user=user,
                journal=self.journal,
                role=role_code,
                responsibilities=(
                    [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE]
                    if role_code == "associate_editor"
                    else []
                ),
                public_profile={
                    "public_name": user.display_name,
                    "public_role_label": {
                        "chief_editor": "主编辑",
                        "executive_editor": "常务副编辑",
                        "associate_editor": "副编辑",
                    }[role_code],
                },
            )
        elif role_code == "unassigned":
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="wagtailadmin",
                    codename="access_admin",
                )
            )
        elif role_code == "technical_superuser":
            user.is_superuser = True
            user.save(update_fields=("is_superuser",))
        return get_user_model().objects.get(pk=user.pk)

    def make_job(self, *, operator, scope=ArticleImportScope.GLOBAL, journal=None):
        job = ArticleImportJob.objects.create(
            package_name="permission.csv",
            status=ImportJobStatus.READY,
            operator=operator,
            import_scope=scope,
            target_journal=journal,
            source_sha256="0" * 64,
        )
        job.error_report.save(
            "errors.csv", ContentFile(b"row_no,error_code\n2,ERROR\n"), save=True
        )
        return job

    def test_simple_roles_match_import_permission_and_button_matrix(self):
        expected = {
            "super_admin": True,
            "chief_editor": True,
            "executive_editor": True,
            "associate_editor": True,
            "unassigned": False,
            "technical_superuser": False,
        }
        for role_code, allowed in expected.items():
            with self.subTest(role=role_code):
                user = self.make_role_user(role_code)
                self.assertEqual(can_import_articles(user), allowed)
                self.client.force_login(user)
                query = (
                    {}
                    if role_code in {"super_admin", "technical_superuser", "unassigned"}
                    else {
                        "scope": ArticleImportScope.JOURNAL,
                        "journal": self.journal.pk,
                    }
                )
                response = self.client.get(reverse("article_admin:import"), query)
                self.assertEqual(response.status_code, 200 if allowed else 403)
                template_response = self.client.get(
                    reverse("article_admin:import_template"), query
                )
                self.assertEqual(template_response.status_code, 200 if allowed else 403)

                article_list = self.client.get(reverse("article_admin:index"))
                if article_list.status_code == 200:
                    if allowed:
                        self.assertContains(article_list, "一键导入文章")
                    else:
                        self.assertNotContains(article_list, "一键导入文章")

    def test_only_business_super_admin_can_view_other_users_global_jobs(self):
        owner = self.make_role_user("unassigned", "owner")
        technical_superuser = self.make_role_user("technical_superuser", "technical")
        super_admin = self.make_role_user("super_admin", "admin")
        job = self.make_job(operator=owner)

        self.client.force_login(technical_superuser)
        self.assertEqual(
            self.client.get(
                reverse("article_admin:import_status"), {"job_id": job.pk}
            ).status_code,
            403,
        )
        self.client.force_login(super_admin)
        response = self.client.get(
            reverse("article_admin:import_status"), {"job_id": job.pk}
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            reverse("article_admin:import_errors", args=[job.pk])
        )
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_unassigned_user_cannot_access_article_import_jobs(self):
        owner = self.make_role_user("unassigned", "owner")
        other = self.make_role_user("unassigned", "other")
        job = self.make_job(operator=owner)
        self.client.force_login(other)

        self.assertEqual(
            self.client.get(reverse("article_admin:import")).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("article_admin:import_status"), {"job_id": job.pk}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("article_admin:import_errors", args=[job.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("article_admin:import_confirm"), {"job_id": job.pk}
            ).status_code,
            403,
        )

    def test_only_global_admin_can_force_suspicious_text(self):
        associate = self.make_role_user("associate_editor", "content")
        super_admin = self.make_role_user("super_admin", "admin")
        job = preview_article_import(
            csv_upload(suspicious=True),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=super_admin,
        )

        self.assertFalse(can_override_suspicious_article_text(associate))
        with self.assertRaises(PermissionDenied):
            confirm_article_import(
                job,
                operator=associate,
                allow_suspicious_text=True,
                override_reason="Verified against trusted source",
            )

        self.assertTrue(can_override_suspicious_article_text(super_admin))
        confirmed = confirm_article_import(
            job,
            operator=super_admin,
            allow_suspicious_text=True,
            override_reason="Verified against trusted source",
        )
        self.assertEqual(confirmed.status, ImportJobStatus.PENDING)
        self.assertEqual(confirmed.confirmed_by, super_admin)

    def test_journal_job_cannot_be_opened_by_removing_or_changing_query_scope(self):
        owner = self.make_role_user("associate_editor", "scope")
        job = self.make_job(
            operator=owner,
            scope=ArticleImportScope.JOURNAL,
            journal=self.journal,
        )
        self.client.force_login(owner)
        import_url = reverse("article_admin:import")

        self.assertEqual(self.client.get(import_url, {"job": job.pk}).status_code, 302)
        self.assertEqual(
            self.client.get(
                import_url, {"job": job.pk, "journal": self.other_journal.pk}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                import_url, {"job": job.pk, "journal": self.journal.pk}
            ).status_code,
            200,
        )

    def test_editor_cannot_view_another_editors_recent_import_jobs(self):
        owner = self.make_role_user("associate_editor", "recent-owner")
        viewer = self.make_role_user("associate_editor", "recent-viewer")
        self.make_job(
            operator=owner,
            scope=ArticleImportScope.JOURNAL,
            journal=self.journal,
        )
        self.client.force_login(viewer)

        response = self.client.get(
            reverse("article_admin:import"), {"journal": self.journal.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["recent_jobs"]), [])

    def test_confirm_view_queues_pending_job_and_start_failure_is_terminal(self):
        owner = self.make_role_user("super_admin", "confirm")
        job = preview_article_import(
            csv_upload(),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=owner,
        )
        self.client.force_login(owner)
        with patch(
            "ai_author_forum.articles.import_views.start_article_import_process"
        ) as start_process:
            response = self.client.post(
                reverse("article_admin:import_confirm"), {"job_id": job.pk}
            )
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.PENDING)
        start_process.assert_called_once()

        failed_job = preview_article_import(
            csv_upload(slug="start-failure"),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=owner,
        )
        with patch(
            "ai_author_forum.articles.import_views.start_article_import_process",
            side_effect=OSError("worker unavailable"),
        ):
            response = self.client.post(
                reverse("article_admin:import_confirm"), {"job_id": failed_job.pk}
            )
        self.assertEqual(response.status_code, 302)
        failed_job.refresh_from_db()
        self.assertEqual(failed_job.status, ImportJobStatus.FAILED)
        self.assertIn("后台任务启动失败", failed_job.notes)
