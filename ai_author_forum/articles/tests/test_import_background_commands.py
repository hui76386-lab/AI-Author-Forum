from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    confirm_article_import,
    create_article_import_preview_job,
    preview_article_import,
)
from ai_author_forum.journals import publishing
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalStatus,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.test_helpers import grant_business_super_admin

SOURCE = (
    "journal_slug,title,slug,article_type,authors,body_html\n"
    'background-journal,Background article,background-article,news,Author,"<p>Body</p>"\n'
).encode("utf-8-sig")


class BackgroundCommandTestMixin:
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.queue_root = Path(self.tempdir.name, "queue")
        self.queue_root.mkdir()
        settings_override = override_settings(
            MEDIA_ROOT=str(Path(self.tempdir.name, "media")),
            AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT=str(self.queue_root),
            ARTICLE_IMPORT_PREVIEW_TIMEOUT_SECONDS=60,
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.user = get_user_model().objects.create_superuser(
            username=f"background-command-{self.__class__.__name__.lower()}",
            email="background@example.com",
            password="test",
        )
        grant_business_super_admin(self.user)
        self.journal = Journal.objects.create(
            name="Background Journal",
            slug="background-journal",
            az_group="B",
            status=JournalStatus.ACTIVE,
        )

    def write_queue_package(self, content=SOURCE, name="articles.csv") -> Path:
        package = self.queue_root / name
        package.write_bytes(content)
        return package

    def make_pending_preview_job(self) -> ArticleImportJob:
        return create_article_import_preview_job(
            SimpleUploadedFile("articles.csv", SOURCE, content_type="text/csv"),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.user,
        )

    def make_confirmed_job(self) -> ArticleImportJob:
        job = preview_article_import(
            SimpleUploadedFile("articles.csv", SOURCE, content_type="text/csv"),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.user,
        )
        return confirm_article_import(job, operator=self.user)


class ArticlePreviewBackgroundCommandTests(BackgroundCommandTestMixin, TestCase):
    def run_command(self, job, package, *, operator_id=None):
        return call_command(
            "preview_article_package",
            package=str(package),
            job_id=job.pk,
            operator_id=self.user.pk if operator_id is None else operator_id,
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

    def test_path_outside_queue_is_rejected_without_deleting_outside_file(self):
        job = self.make_pending_preview_job()
        outside = Path(self.tempdir.name, "outside.csv")
        outside.write_bytes(SOURCE)

        with self.assertRaises(CommandError):
            self.run_command(job, outside)

        self.assertTrue(outside.exists())
        self.assert_failed_input_job(job)

    def test_missing_queue_file_marks_job_failed(self):
        job = self.make_pending_preview_job()

        with self.assertRaises(CommandError):
            self.run_command(job, self.queue_root / "missing.csv")

        self.assert_failed_input_job(job)

    def test_missing_operator_deletes_safe_queue_file_and_marks_job_failed(self):
        job = self.make_pending_preview_job()
        package = self.write_queue_package()

        with self.assertRaisesMessage(
            CommandError, "Article import preview operator does not exist."
        ):
            self.run_command(job, package, operator_id=999999)

        self.assertFalse(package.exists())
        self.assert_failed_input_job(job)

    def test_success_deletes_queue_file(self):
        job = self.make_pending_preview_job()
        package = self.write_queue_package()

        self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.READY)
        self.assertFalse(package.exists())

    def test_hash_failure_deletes_queue_file(self):
        job = self.make_pending_preview_job()
        package = self.write_queue_package(SOURCE + b"changed")

        with self.assertRaises(CommandError):
            self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.FAILED)
        self.assertFalse(package.exists())

    def test_cleanup_audit_failure_does_not_mask_original_command_error(self):
        job = self.make_pending_preview_job()
        package = self.write_queue_package(SOURCE + b"changed")

        with patch(
            "ai_author_forum.articles.management.commands.preview_article_package.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesMessage(CommandError, "队列文件哈希与任务记录不一致"):
                self.run_command(job, package)

        self.assertFalse(package.exists())


class ArticleImportBackgroundCommandTests(BackgroundCommandTestMixin, TestCase):
    def run_command(self, job, package, *, operator_id=None):
        return call_command(
            "import_article_package",
            package=str(package),
            preview_job_id=job.pk,
            operator_id=self.user.pk if operator_id is None else operator_id,
        )

    def test_missing_operator_deletes_safe_queue_file_and_marks_job_failed(self):
        job = self.make_confirmed_job()
        package = self.write_queue_package()

        with self.assertRaisesMessage(
            CommandError, "Article import operator does not exist."
        ):
            self.run_command(job, package, operator_id=999999)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.FAILED)
        self.assertFalse(package.exists())

    def test_success_deletes_queue_file(self):
        job = self.make_confirmed_job()
        package = self.write_queue_package()

        self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.COMPLETED)
        self.assertFalse(package.exists())

    def test_hash_failure_deletes_queue_file(self):
        job = self.make_confirmed_job()
        package = self.write_queue_package(SOURCE + b"changed")

        with self.assertRaises(CommandError):
            self.run_command(job, package)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.FAILED)
        self.assertFalse(package.exists())

    def test_cleanup_audit_failure_does_not_mask_original_command_error(self):
        job = self.make_confirmed_job()
        package = self.write_queue_package(SOURCE + b"changed")

        with patch(
            "ai_author_forum.articles.management.commands.import_article_package.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesMessage(
                CommandError,
                "Queued package hash does not match the locked preview job.",
            ):
                self.run_command(job, package)

        self.assertFalse(package.exists())


class ArticleImportCleanupCommandTests(BackgroundCommandTestMixin, TestCase):
    def make_job(self, *, status, digest, started_at=None):
        return ArticleImportJob.objects.create(
            package_name=f"{status}.csv",
            status=status,
            operator=self.user,
            source_sha256=digest,
            source_format="csv",
            started_at=started_at,
        )

    def write_stale_file(self, name: str, content: bytes) -> Path:
        path = self.write_queue_package(content, name)
        old = (timezone.now() - timedelta(minutes=10)).timestamp()
        os.utime(path, (old, old))
        return path

    @override_settings(ARTICLE_IMPORT_PREVIEW_TIMEOUT_SECONDS=60)
    def test_cleanup_removes_orphan_preserves_active_hashes_and_fails_timeout(self):
        active_files = {}
        for status in (
            ImportJobStatus.PENDING,
            ImportJobStatus.VALIDATING,
            ImportJobStatus.IMPORTING,
        ):
            content = f"active-{status}".encode()
            digest = hashlib.sha256(content).hexdigest()
            active_files[status] = self.write_stale_file(
                f"active-{status}.bin", content
            )
            self.make_job(
                status=status,
                digest=digest,
                started_at=(
                    timezone.now() if status == ImportJobStatus.VALIDATING else None
                ),
            )

        orphan = self.write_stale_file("private-orphan-name.bin", b"orphan")
        stale_job = self.make_job(
            status=ImportJobStatus.VALIDATING,
            digest=hashlib.sha256(b"stale-job").hexdigest(),
            started_at=timezone.now() - timedelta(minutes=10),
        )

        call_command("cleanup_article_import_previews")

        stale_job.refresh_from_db()
        self.assertEqual(stale_job.status, ImportJobStatus.FAILED)
        self.assertIsNotNone(stale_job.finished_at)
        self.assertFalse(orphan.exists())
        self.assertTrue(all(path.exists() for path in active_files.values()))

        audit = AuditLog.objects.get(
            target_type="ArticleImportQueue", target_id="stale-cleanup"
        )
        self.assertEqual(audit.metadata["timed_out_jobs"], 1)
        self.assertEqual(audit.metadata["removed_queue_files"], 1)
        serialized = json.dumps(audit.metadata, ensure_ascii=False)
        self.assertNotIn("private-orphan-name.bin", serialized)
        self.assertNotIn(str(self.queue_root), serialized)


class ArticleImportLauncherTests(BackgroundCommandTestMixin, TestCase):
    def assert_safe_popen(self, mocked_popen, *, command_name):
        args, kwargs = mocked_popen.call_args
        command = args[0]
        self.assertIsInstance(command, list)
        self.assertIn(command_name, command)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("--target-journal-id", command)
        self.assertNotIn("--allow-suspicious-text", command)
        self.assertNotIn("--override-reason", command)
        if os.name == "nt":
            self.assertEqual(
                kwargs["creationflags"], publishing.subprocess.CREATE_NO_WINDOW
            )

    def test_preview_launcher_uses_argument_array_and_hidden_windows_process(self):
        package = self.write_queue_package()
        with patch.object(publishing.subprocess, "Popen") as mocked_popen:
            publishing.start_article_import_preview_process(
                package_path=package,
                job_id=17,
                operator_id=self.user.pk,
            )

        self.assert_safe_popen(mocked_popen, command_name="preview_article_package")
        command = mocked_popen.call_args.args[0]
        self.assertEqual(command[command.index("--job-id") + 1], "17")

    def test_import_launcher_has_no_business_override_arguments(self):
        package = self.write_queue_package()
        with patch.object(publishing.subprocess, "Popen") as mocked_popen:
            publishing.start_article_import_process(
                package_path=package,
                preview_job_id=23,
                operator_id=self.user.pk,
            )

        self.assert_safe_popen(mocked_popen, command_name="import_article_package")
        command = mocked_popen.call_args.args[0]
        self.assertEqual(command[command.index("--preview-job-id") + 1], "23")
