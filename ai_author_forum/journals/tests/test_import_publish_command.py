from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ImportJobStatus,
    Journal,
    JournalImportJob,
)
from ai_author_forum.journals.services import import_package
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.models import (
    StaticManifest,
    StaticPublishJob,
    StaticPublishTarget,
)
from ai_author_forum.static_publish.services import StaticPublisher
from ai_author_forum.static_publish.tests.providers import TARGETS
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)

PROVIDER = "ai_author_forum.static_publish.tests.providers.TestTargetProvider"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class ImportPublishCommandTests(TestCase):
    def setUp(self):
        from unittest.mock import patch

        snapshot_patcher = patch.object(
            StaticPublisher, "_configure_snapshot_transaction", return_value=None
        )
        snapshot_patcher.start()
        self.addCleanup(snapshot_patcher.stop)
        self.publish_root = TemporaryDirectory()
        self.media_root = TemporaryDirectory()
        self.package_root = TemporaryDirectory()
        self.addCleanup(self.package_root.cleanup)
        self.addCleanup(self.media_root.cleanup)
        self.addCleanup(self.publish_root.cleanup)
        self.settings_override = override_settings(
            STATIC_PUBLISH_ROOT=self.publish_root.name,
            STATIC_PUBLISH_TARGET_PROVIDER=PROVIDER,
            STATIC_PUBLISH_KEEP_RELEASES=5,
            MEDIA_ROOT=self.media_root.name,
            STORAGES=STORAGES,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        TARGETS.clear()
        self.addCleanup(TARGETS.clear)
        self.operator = get_user_model().objects.create_superuser(
            username="import-publisher",
            email="publisher@example.com",
            password="secret",
        )
        grant_business_super_admin(self.operator)
        self.package_path = self._build_package()

    def _build_package(self):
        package_path = Path(self.package_root.name, "journal-package.zip")
        with ZipFile(package_path, "w") as archive:
            journal_workbook = Workbook()
            journal_sheet = journal_workbook.active
            journal_sheet.append(["journal_name", "slug", "az_group", "status"])
            journal_sheet.append(["AI Ethics Forum", "ai-ethics-forum", "A", "active"])
            journal_stream = BytesIO()
            journal_workbook.save(journal_stream)
            archive.writestr("journals.xlsx", journal_stream.getvalue())

            article_workbook = Workbook()
            article_sheet = article_workbook.active
            article_sheet.append(
                ["journal_slug", "title", "slug", "article_type", "body_html"]
            )
            article_sheet.append(
                [
                    "ai-ethics-forum",
                    "Responsible Co-authoring",
                    "responsible-co-authoring",
                    "ai_article",
                    "<html><body><h1>Responsible Co-authoring</h1></body></html>",
                ]
            )
            article_stream = BytesIO()
            article_workbook.save(article_stream)
            archive.writestr("articles.xlsx", article_stream.getvalue())
        return package_path

    def _ensure_publishable_article(self):
        article = ArticlePage.objects.filter(slug="existing-approved-article").first()
        if article is not None:
            return article
        journal = Journal.objects.create(
            name="Existing Approved Journal",
            slug="existing-approved-journal",
            az_group="E",
            status="active",
        )
        article = ArticlePage(
            title="Existing approved article",
            slug="existing-approved-article",
            abstract="Approved content available before this import.",
            body=[("paragraph", "<p>Approved content.</p>")],
            authors="Editorial Team",
            keywords="AI",
            primary_journal=journal,
            owner=self.operator,
        )
        Page.get_first_root_node().add_child(instance=article)
        formally_approve_test_article(article, actor=self.operator)
        slot = LayoutSlot.objects.create(
            code="import-command-home",
            title="Import command home",
            scope=LayoutSlot.Scope.HOME,
            max_items=5,
        )
        ArticlePlacement.objects.create(
            article=article,
            slot=slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
            target_slug="",
        )
        return article

    def _import_and_publish(self):
        self._ensure_publishable_article()
        call_command(
            "import_journal_package",
            "--package",
            str(self.package_path),
            "--publish-static-site",
            "--operator-id",
            str(self.operator.pk),
            verbosity=0,
        )
        return StaticPublishJob.objects.order_by("pk").last()

    def test_publish_is_refused_when_import_only_created_drafts(self):
        TARGETS["/"] = b"<html>must not publish</html>"

        with self.assertRaisesMessage(CommandError, "Imported articles remain drafts"):
            call_command(
                "import_journal_package",
                "--package",
                str(self.package_path),
                "--publish-static-site",
                "--operator-id",
                str(self.operator.pk),
                verbosity=0,
            )

        self.assertFalse(StaticPublishJob.objects.exists())
        imported = ArticlePage.objects.get(
            source_static_article__slug="responsible-co-authoring"
        )
        self.assertEqual(imported.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertFalse(imported.live)
        self.assertFalse(imported.placements.exists())

    def test_import_creates_visible_central_publish_release(self):
        TARGETS["/"] = b"<html>import release</html>"

        job = self._import_and_publish()

        self.assertEqual(StaticPublishJob.objects.count(), 1)
        self.assertEqual(job.scope, StaticPublishJob.Scope.FULL)
        self.assertEqual(job.status, StaticPublishJob.Status.SUCCEEDED)
        self.assertEqual(job.triggered_by, self.operator)
        current = Path(self.publish_root.name, "current")
        self.assertEqual((current / "index.html").read_bytes(), TARGETS["/"])
        self.assertTrue((current / "manifest.json").is_file())
        manifest = StaticManifest.objects.get(job=job)
        self.assertTrue(manifest.is_active)
        imported = ArticlePage.objects.get(
            source_static_article__slug="responsible-co-authoring"
        )
        self.assertEqual(imported.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertFalse(imported.live)
        self.assertFalse(imported.placements.exists())
        self.assertEqual(
            JournalImportJob.objects.get().summary["static_publish_job"],
            {"id": job.pk, "status": job.status, "version": job.version},
        )
        self.assertEqual(
            ArticleImportJob.objects.get().summary["static_publish_job"],
            {"id": job.pk, "status": job.status, "version": job.version},
        )
        self.assertEqual(
            list(
                AuditLog.objects.filter(action=AuditAction.PUBLISH, actor=self.operator)
                .order_by("created_at", "pk")
                .values_list("status", flat=True)
            ),
            [AuditStatus.STARTED, AuditStatus.SUCCESS],
        )

        self.client.force_login(self.operator)
        response = self.client.get(reverse("static_publish:center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"#{job.pk}")
        self.assertContains(response, job.version)

    def test_confirmed_preview_is_linked_to_import_and_static_publish_jobs(self):
        with self.package_path.open("rb") as source_file:
            preview = import_package(source_file, operator=self.operator, dry_run=True)
        TARGETS["/"] = b"<html>confirmed preview release</html>"
        self._ensure_publishable_article()

        call_command(
            "import_journal_package",
            "--package",
            str(self.package_path),
            "--publish-static-site",
            "--operator-id",
            str(self.operator.pk),
            "--preview-journal-job-id",
            str(preview.journal_job.pk),
            "--preview-article-job-id",
            str(preview.article_job.pk),
            verbosity=0,
        )

        preview.journal_job.refresh_from_db()
        preview.article_job.refresh_from_db()
        publish_job = StaticPublishJob.objects.get()
        self.assertEqual(preview.journal_job.status, ImportJobStatus.COMPLETED)
        self.assertEqual(preview.article_job.status, ImportJobStatus.COMPLETED)
        self.assertEqual(
            preview.journal_job.summary["confirmed_import_job"]["status"],
            ImportJobStatus.COMPLETED,
        )
        self.assertEqual(
            preview.article_job.summary["confirmed_import_job"]["status"],
            ImportJobStatus.COMPLETED,
        )
        self.assertEqual(
            preview.journal_job.summary["static_publish_job"]["id"],
            publish_job.pk,
        )
        self.assertEqual(
            preview.article_job.summary["static_publish_job"]["id"],
            publish_job.pk,
        )

    def test_failed_import_publish_is_recorded_and_can_be_retried(self):
        TARGETS["/"] = RuntimeError("temporary render failure")

        with self.assertRaisesMessage(CommandError, "Static publish job #"):
            self._import_and_publish()

        failed_job = StaticPublishJob.objects.get()
        self.assertEqual(failed_job.status, StaticPublishJob.Status.FAILED)
        failed_target = failed_job.targets.get()
        self.assertEqual(failed_target.status, StaticPublishTarget.Status.FAILED)
        self.assertIn("temporary render failure", failed_target.error)
        self.assertEqual(
            JournalImportJob.objects.get().summary["static_publish_job"]["id"],
            failed_job.pk,
        )
        self.assertEqual(
            JournalImportJob.objects.get().summary["static_publish_job"]["status"],
            StaticPublishJob.Status.FAILED,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(failed_job.pk),
            ).exists()
        )

        self.client.force_login(self.operator)
        detail = self.client.get(
            reverse("static_publish:job_detail", kwargs={"job_id": failed_job.pk})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "temporary render failure")
        self.assertContains(detail, "重试选中的失败目标")

        TARGETS["/"] = b"<html>recovered import release</html>"
        retry_job = StaticPublisher().retry(failed_job, self.operator)
        retry_job.refresh_from_db()

        self.assertEqual(retry_job.retry_of, failed_job)
        self.assertEqual(retry_job.status, StaticPublishJob.Status.SUCCEEDED)
        self.assertEqual(
            Path(self.publish_root.name, "current", "index.html").read_bytes(),
            TARGETS["/"],
        )
        self.assertTrue(StaticManifest.objects.get(job=retry_job).is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.RETRY,
                status=AuditStatus.SUCCESS,
                target_id=str(retry_job.pk),
            ).exists()
        )

    def test_import_publish_release_can_be_rolled_back(self):
        TARGETS["/"] = b"<html>import release v1</html>"
        first_job = self._import_and_publish()
        TARGETS["/"] = b"<html>import release v2</html>"
        second_job = self._import_and_publish()
        self.assertNotEqual(first_job.version, second_job.version)

        rollback_job = StaticPublisher().rollback(
            first_job.version, self.operator, reason="回滚到已验证的导入发布版本"
        )
        rollback_job.refresh_from_db()

        self.assertEqual(rollback_job.status, StaticPublishJob.Status.ROLLED_BACK)
        self.assertEqual(rollback_job.rollback_version, first_job.version)
        self.assertEqual(
            Path(self.publish_root.name, "current", "index.html").read_bytes(),
            b"<html>import release v1</html>",
        )
        self.assertTrue(StaticManifest.objects.get(job=first_job).is_active)
        self.assertFalse(StaticManifest.objects.get(job=second_job).is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.ROLLBACK,
                status=AuditStatus.SUCCESS,
                target_id=str(rollback_job.pk),
            ).exists()
        )
