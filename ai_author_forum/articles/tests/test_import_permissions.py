from __future__ import annotations

import csv
import io
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalStatus,
)
from ai_author_forum.site_settings.management.commands.seed_roles import (
    ROLE_DEFINITIONS,
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

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tempdir.cleanup)

    def make_role_user(self, role_code, suffix=""):
        definition = ROLE_DEFINITIONS[role_code]
        user = get_user_model().objects.create_user(
            username=f"article-import-{role_code}-{suffix or get_user_model().objects.count()}",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=definition["display_name"]))
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

    def test_all_standard_roles_match_import_permission_and_button_matrix(self):
        expected = {
            "project_lead": True,
            "super_admin": True,
            "content_manager": False,
            "reviewer": False,
            "site_operator": False,
            "publisher": False,
            "readonly": False,
        }
        for role_code, allowed in expected.items():
            with self.subTest(role=role_code):
                user = self.make_role_user(role_code)
                self.assertEqual(can_import_articles(user), allowed)
                self.client.force_login(user)
                response = self.client.get(reverse("article_admin:import"))
                self.assertEqual(response.status_code, 200 if allowed else 403)
                template_response = self.client.get(
                    reverse("article_admin:import_template")
                )
                self.assertEqual(template_response.status_code, 200 if allowed else 403)

                article_list = self.client.get(reverse("article_admin:index"))
                if article_list.status_code == 200:
                    if allowed:
                        self.assertContains(article_list, "一键导入文章")
                    else:
                        self.assertNotContains(article_list, "一键导入文章")

    def test_non_superuser_global_roles_can_view_other_users_jobs_and_errors(self):
        owner = self.make_role_user("content_manager", "owner")
        project_lead = self.make_role_user("project_lead", "lead")
        super_admin = self.make_role_user("super_admin", "admin")
        job = self.make_job(operator=owner)

        for user in (project_lead, super_admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse("article_admin:import_status"), {"job_id": job.pk}
                )
                self.assertEqual(response.status_code, 200)
                response = self.client.get(
                    reverse("article_admin:import_errors", args=[job.pk])
                )
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_content_manager_cannot_access_article_import_jobs(self):
        owner = self.make_role_user("content_manager", "owner")
        other = self.make_role_user("content_manager", "other")
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
        content_manager = self.make_role_user("content_manager", "content")
        project_lead = self.make_role_user("project_lead", "lead")
        job = preview_article_import(
            csv_upload(suspicious=True),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=project_lead,
        )

        self.assertFalse(can_override_suspicious_article_text(content_manager))
        with self.assertRaises(PermissionDenied):
            confirm_article_import(
                job,
                operator=content_manager,
                allow_suspicious_text=True,
                override_reason="Verified against trusted source",
            )

        self.assertTrue(can_override_suspicious_article_text(project_lead))
        confirmed = confirm_article_import(
            job,
            operator=project_lead,
            allow_suspicious_text=True,
            override_reason="Verified against trusted source",
        )
        self.assertEqual(confirmed.status, ImportJobStatus.PENDING)
        self.assertEqual(confirmed.confirmed_by, project_lead)

    def test_journal_job_cannot_be_opened_by_removing_or_changing_query_scope(self):
        owner = self.make_role_user("content_manager", "scope")
        job = self.make_job(
            operator=owner,
            scope=ArticleImportScope.JOURNAL,
            journal=self.journal,
        )
        self.client.force_login(owner)
        import_url = reverse("article_admin:import")

        self.assertEqual(self.client.get(import_url, {"job": job.pk}).status_code, 403)
        self.assertEqual(
            self.client.get(
                import_url, {"job": job.pk, "journal": self.other_journal.pk}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                import_url, {"job": job.pk, "journal": self.journal.pk}
            ).status_code,
            403,
        )

    def test_content_manager_cannot_view_recent_article_import_jobs(self):
        content_manager = self.make_role_user("content_manager", "recent")
        self.make_job(operator=content_manager)
        self.client.force_login(content_manager)

        response = self.client.get(reverse("article_admin:import"))

        self.assertEqual(response.status_code, 403)

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
