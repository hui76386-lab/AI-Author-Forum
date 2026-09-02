import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_author_forum.articles.import_services import execute_article_import_preview
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ArticleType,
    ImportJobStatus,
    ImportRowStatus,
    Journal,
    JournalStatus,
)
from ai_author_forum.test_helpers import grant_business_super_admin


class ArticleImportViewTests(TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        root = Path(self.temp_directory.name)
        self.settings_override = override_settings(
            MEDIA_ROOT=root / "media",
            AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT=root / "queue",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.admin = get_user_model().objects.create_superuser(
            username="import-view-admin", email="view@example.com", password="test"
        )
        grant_business_super_admin(self.admin)
        self.staff = get_user_model().objects.create_user(
            username="no-import",
            email="no-import@example.com",
            display_name="No Import",
            password="test",
            is_staff=True,
        )
        self.staff.user_permissions.add(
            Permission.objects.get(codename="access_admin"),
            Permission.objects.get(
                content_type__app_label="site_settings", codename="access_articles"
            ),
        )
        self.journal = Journal.objects.create(
            name="View Journal",
            slug="view-journal",
            az_group="V",
            status=JournalStatus.ACTIVE,
        )

    def test_import_urls_require_permission(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("article_admin:import")).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("article_admin:import_template")).status_code, 403
        )

    def test_global_and_journal_pages_render_locked_scope(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("article_admin:import"))
        self.assertContains(response, "文章批量导入中心")
        self.assertContains(response, "全局文章导入")
        response = self.client.get(
            reverse("article_admin:import"), {"journal": self.journal.pk}
        )
        self.assertContains(response, "本刊文章导入")
        self.assertContains(response, self.journal.name)
        self.assertContains(response, "已锁定")

    def test_entry_buttons_are_visible_only_with_full_permission(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("article_admin:index"))
        self.assertContains(response, "一键导入文章")
        response = self.client.get(
            reverse("journals:workspace", args=[self.journal.pk])
        )
        self.assertContains(response, "导入本刊文章")

        self.client.force_login(self.staff)
        response = self.client.get(reverse("article_admin:index"))
        self.assertNotContains(response, "一键导入文章", status_code=200)

    @patch("ai_author_forum.articles.import_views.start_article_import_preview_process")
    def test_document_post_redirects_to_get_without_duplicate_jobs(self, start_preview):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile(
            "safe.md",
            b"# Safe article\n\nThis is safe Markdown body content.",
            content_type="text/markdown",
        )
        response = self.client.post(
            reverse("article_admin:import"),
            {
                "source_file": upload,
                "default_journal": self.journal.pk,
                "document_title": "Safe article",
                "document_slug": "safe-article",
                "document_article_type": ArticleType.AI_ARTICLE,
                "document_authors": "Codex QA",
                "csv_encoding": "auto",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = ArticleImportJob.objects.get()
        expected_url = f'{reverse("article_admin:import")}?job={job.pk}'
        self.assertEqual(response["Location"], expected_url)
        start_preview.assert_called_once()

        for _ in range(2):
            get_response = self.client.get(expected_url)
            self.assertEqual(get_response.status_code, 200)
            self.assertContains(get_response, "safe.md")
        self.assertEqual(ArticleImportJob.objects.count(), 1)

        execute_article_import_preview(job, operator=self.admin)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.READY)
        row = job.rows.get()
        self.assertEqual(row.status, ImportRowStatus.SUCCESS)
        self.assertEqual(row.normalized_data["article_type"], ArticleType.AI_ARTICLE)

        ready_response = self.client.get(expected_url)
        self.assertEqual(ready_response.status_code, 200)
        self.assertContains(ready_response, "Safe article")
        self.assertContains(ready_response, ArticleType.AI_ARTICLE)
        self.assertEqual(ArticleImportJob.objects.count(), 1)

    @patch("ai_author_forum.articles.import_views.start_article_import_preview_process")
    def test_journal_document_post_keeps_server_locked_scope(self, start_preview):
        self.client.force_login(self.admin)
        other_journal = Journal.objects.create(
            name="Other Journal",
            slug="other-journal",
            az_group="O",
            status=JournalStatus.ACTIVE,
        )
        upload = SimpleUploadedFile(
            "journal.md",
            b"# Journal article\n\nServer-locked journal body.",
            content_type="text/markdown",
        )
        response = self.client.post(
            f'{reverse("article_admin:import")}?journal={self.journal.pk}',
            {
                "source_file": upload,
                "default_journal": other_journal.pk,
                "document_title": "Journal article",
                "document_slug": "journal-article",
                "document_article_type": ArticleType.NEWS,
                "document_authors": "Codex QA",
                "csv_encoding": "auto",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = ArticleImportJob.objects.get()
        self.assertEqual(job.target_journal, self.journal)
        self.assertIsNone((job.summary or {}).get("default_journal_id"))
        self.assertEqual(
            response["Location"],
            f'{reverse("article_admin:import")}?journal={self.journal.pk}&job={job.pk}',
        )
        start_preview.assert_called_once()
