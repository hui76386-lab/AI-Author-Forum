from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    confirm_article_import,
    preview_article_import,
)
from ai_author_forum.journals.models import (
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalStatus,
    StaticArticle,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.test_helpers import grant_business_super_admin

SOURCE = (
    "journal_slug,title,slug,article_type,authors,body_html\n"
    'command-journal,Command article,command-article,news,Author,"<p>Body</p>"\n'
).encode("utf-8-sig")


class ArticleImportCommandTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.queue_root = Path(self.tempdir.name, "queue")
        self.queue_root.mkdir()
        settings_override = override_settings(
            MEDIA_ROOT=str(Path(self.tempdir.name, "media")),
            AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT=str(self.queue_root),
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.user = get_user_model().objects.create_superuser(
            username="article-import-command-admin",
            email="command@example.com",
            password="test",
        )
        grant_business_super_admin(self.user)
        self.journal = Journal.objects.create(
            name="Command Journal",
            slug="command-journal",
            az_group="C",
            status=JournalStatus.ACTIVE,
        )
        self.other = Journal.objects.create(
            name="Other Command Journal",
            slug="other-command-journal",
            az_group="O",
            status=JournalStatus.ACTIVE,
        )

    def make_pending_job(self, *, scope=ArticleImportScope.GLOBAL):
        context = ArticleImportContext(
            scope=scope,
            target_journal_id=(
                self.journal.pk if scope == ArticleImportScope.JOURNAL else None
            ),
        )
        job = preview_article_import(
            SimpleUploadedFile("articles.csv", SOURCE, content_type="text/csv"),
            context=context,
            operator=self.user,
        )
        confirm_article_import(job, operator=self.user)
        return job

    def write_queue_package(self, content=SOURCE, name="articles.csv"):
        package = self.queue_root / name
        package.write_bytes(content)
        return package

    def run_command(self, job, package):
        return call_command(
            "import_article_package",
            package=str(package),
            operator_id=self.user.pk,
            preview_job_id=job.pk,
        )

    def assert_failed_input_job(self, job):
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.FAILED)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(job.pk),
                status="failure",
                metadata__error_code="ARTICLE_IMPORT_COMMAND_INPUT_INVALID",
            ).exists()
        )

    def test_path_outside_queue_marks_pending_job_failed(self):
        job = self.make_pending_job()
        outside = Path(self.tempdir.name, "outside.csv")
        outside.write_bytes(SOURCE)

        with self.assertRaises(CommandError):
            self.run_command(job, outside)

        self.assert_failed_input_job(job)

    def test_missing_queue_file_marks_pending_job_failed(self):
        job = self.make_pending_job()

        with self.assertRaises(CommandError):
            self.run_command(job, self.queue_root / "missing.csv")

        self.assert_failed_input_job(job)

    def test_hash_mismatch_marks_pending_job_failed(self):
        job = self.make_pending_job()
        package = self.write_queue_package(SOURCE + b"changed")
        self.assertNotEqual(
            hashlib.sha256(package.read_bytes()).hexdigest(), job.source_sha256
        )

        with self.assertRaises(CommandError):
            self.run_command(job, package)

        self.assert_failed_input_job(job)

    def test_command_has_no_target_journal_override_and_uses_locked_job_scope(self):
        command = __import__(
            "ai_author_forum.articles.management.commands.import_article_package",
            fromlist=["Command"],
        ).Command()
        parser = command.create_parser("manage.py", "import_article_package")
        option_strings = {
            option for action in parser._actions for option in action.option_strings
        }
        self.assertNotIn("--target-journal-id", option_strings)

        job = self.make_pending_job(scope=ArticleImportScope.JOURNAL)
        package = self.write_queue_package()
        self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.COMPLETED)
        article = StaticArticle.objects.get(slug="command-article")
        self.assertEqual(article.journal_id, self.journal.pk)
        self.assertNotEqual(article.journal_id, self.other.pk)

    def test_valid_command_completes_import(self):
        job = self.make_pending_job()
        package = self.write_queue_package()

        self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.COMPLETED)
        self.assertTrue(
            StaticArticle.objects.filter(
                journal=self.journal, slug="command-article", review_status="draft"
            ).exists()
        )

    def test_completed_job_cannot_be_reexecuted_or_have_terminal_state_rewritten(self):
        job = self.make_pending_job()
        package = self.write_queue_package()
        self.run_command(job, package)
        job.refresh_from_db()
        finished_at = job.finished_at

        with self.assertRaises(CommandError):
            self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.COMPLETED)
        self.assertEqual(job.finished_at, finished_at)
        self.assertEqual(
            StaticArticle.objects.filter(
                journal=self.journal, slug="command-article"
            ).count(),
            1,
        )
