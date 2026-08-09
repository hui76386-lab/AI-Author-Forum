import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import (
    PermissionDenied,
    SuspiciousFileOperation,
    ValidationError,
)
from django.test import TestCase, override_settings
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import record_article_build_success
from ai_author_forum.journals.models import Journal, StaticArticle
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import (
    AuditAction,
    AuditLog,
    AuditStatus,
)
from ai_author_forum.site_settings.services import (
    record_audit_event as persist_audit_event,
)
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)

from ..models import StaticManifest, StaticPublishJob, StaticPublishTarget
from ..providers import output_path_for_url
from ..readiness import ContentReadinessResult, ReadinessFinding
from ..services import (
    AuditWriteError,
    PublishError,
    RenderedTargetSnapshot,
    StaticPublisher,
    safe_relative_path,
)
from .providers import ARTICLE_TARGETS, TARGETS, TestTarget

PROVIDER = "ai_author_forum.static_publish.tests.providers.TestTargetProvider"


@override_settings(
    STATIC_PUBLISH_TARGET_PROVIDER=PROVIDER, STATIC_PUBLISH_KEEP_RELEASES=5
)
class StaticPublisherTests(TestCase):
    def setUp(self):
        snapshot_patcher = patch.object(
            StaticPublisher, "_configure_snapshot_transaction", return_value=None
        )
        snapshot_patcher.start()
        self.addCleanup(snapshot_patcher.stop)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.media_temporary = TemporaryDirectory()
        self.addCleanup(self.media_temporary.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_temporary.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.publisher = StaticPublisher(self.temporary.name)
        self.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="static-publisher-admin",
                email="static-publisher-admin@example.com",
                display_name="Static Publisher Admin",
                password="test-password",
                is_staff=True,
            )
        )
        readiness_patcher = patch(
            "ai_author_forum.static_publish.services.check_content_readiness",
            return_value=ContentReadinessResult(),
        )
        readiness_patcher.start()
        self.addCleanup(readiness_patcher.stop)
        TARGETS.clear()
        TARGETS.update(
            {"/": b"<html>home v1</html>", "/articles/one/": b"<html>one</html>"}
        )

    def build(self, paths=None):
        job = StaticPublishJob.objects.create(
            scope=(
                StaticPublishJob.Scope.SELECTIVE
                if paths
                else StaticPublishJob.Scope.FULL
            ),
            requested_paths=paths or [],
            triggered_by=self.admin,
        )
        manifest = self.publisher.build(job)
        job.refresh_from_db()
        return job, manifest

    def test_full_build_writes_manifest_and_activates_release(self):
        job, manifest = self.build()
        current = Path(self.temporary.name, "current")
        data = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(job.status, StaticPublishJob.Status.SUCCEEDED)
        self.assertEqual(data["version"], job.version)
        self.assertTrue((current / "index.html").is_file())
        self.assertTrue((current / "articles/one/index.html").is_file())
        self.assertEqual(manifest.files, data["files"])
        self.assertEqual(
            manifest.metadata["input_snapshot_at"], data["input_snapshot_at"]
        )
        self.assertNotIn("manifest.json", {item["path"] for item in manifest.files})
        self.assertEqual(job.targets.count(), 2)

    def test_rendering_uses_frozen_target_bytes_after_snapshot(self):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            status=StaticPublishJob.Status.RUNNING,
            version="snapshot-test",
            triggered_by=self.admin,
        )
        snapshots, snapshot_at = self.publisher._snapshot_targets(job, None)
        TARGETS["/"] = b"<html>changed after snapshot</html>"
        staging = Path(self.temporary.name, "snapshot-staging")
        staging.mkdir()

        self.publisher._render_targets(job, snapshots, staging)

        self.assertEqual((staging / "index.html").read_bytes(), b"<html>home v1</html>")
        self.assertIsNotNone(snapshot_at)

    def test_copy_assets_includes_manifest_collected_hashed_assets(self):
        with TemporaryDirectory() as static_root:
            hashed_css = Path(static_root, "css/main.abc123.css")
            hashed_css.parent.mkdir(parents=True, exist_ok=True)
            hashed_css.write_text("body { color: navy; }", encoding="utf-8")
            TARGETS["/"] = (
                b'<html><link rel="stylesheet" href="/static/css/main.abc123.css"></html>'
            )

            with self.settings(STATIC_ROOT=static_root):
                job, _manifest = self.build()

            self.assertEqual(job.status, StaticPublishJob.Status.SUCCEEDED)
            self.assertTrue(
                Path(
                    self.temporary.name,
                    "current/static/css/main.abc123.css",
                ).is_file()
            )

    def test_copy_assets_retries_when_collectstatic_changes_the_tree(self):
        with (
            TemporaryDirectory() as static_root,
            TemporaryDirectory() as media_root,
            TemporaryDirectory() as staging_root,
        ):
            asset = Path(static_root, "wagtailadmin/js/chooser.js")
            asset.parent.mkdir(parents=True)
            asset.write_text("chooser", encoding="utf-8")
            real_copytree = shutil.copytree
            attempts = 0

            def flaky_copytree(source, destination, *args, **kwargs):
                nonlocal attempts
                if Path(source) == Path(static_root):
                    attempts += 1
                    if attempts == 1:
                        raise FileNotFoundError("collectstatic replaced a file")
                return real_copytree(source, destination, *args, **kwargs)

            with (
                self.settings(STATIC_ROOT=static_root, MEDIA_ROOT=media_root),
                patch(
                    "ai_author_forum.static_publish.services.shutil.copytree",
                    side_effect=flaky_copytree,
                ),
                patch("ai_author_forum.static_publish.services.time.sleep") as sleep,
            ):
                self.publisher._copy_assets(Path(staging_root))

            self.assertEqual(attempts, 2)
            sleep.assert_called_once_with(0.1)
            self.assertTrue(
                Path(staging_root, "static/wagtailadmin/js/chooser.js").is_file()
            )

    def test_copy_assets_reports_a_stable_error_after_retry_exhaustion(self):
        with TemporaryDirectory() as static_root, TemporaryDirectory() as staging_root:
            Path(static_root, "present.txt").write_text("asset", encoding="utf-8")
            with (
                self.settings(STATIC_ROOT=static_root),
                patch(
                    "ai_author_forum.static_publish.services.shutil.copytree",
                    side_effect=FileNotFoundError("tree is changing"),
                ) as copytree,
                patch("ai_author_forum.static_publish.services.time.sleep") as sleep,
            ):
                with self.assertRaisesMessage(
                    PublishError,
                    "Collected static assets changed during copy; retry the publish",
                ):
                    self.publisher._copy_assets(Path(staging_root))

            self.assertEqual(copytree.call_count, 5)
            self.assertEqual(sleep.call_count, 4)

    def test_render_failure_without_exception_code_records_fallback_code(self):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            status=StaticPublishJob.Status.RUNNING,
            version="error-code-test",
            triggered_by=self.admin,
        )
        target = TestTarget("/broken/", b"unused")
        record = StaticPublishTarget.objects.create(
            job=job,
            path=target.output_path,
        )
        error = ValidationError("render failed")
        snapshot = RenderedTargetSnapshot(
            target=target,
            record=record,
            content=None,
            error=error,
            duration_ms=0,
        )
        staging = Path(self.temporary.name, "error-code-staging")
        staging.mkdir()

        self.publisher._render_targets(job, [snapshot], staging)

        record = job.targets.get(path=target.output_path)
        self.assertEqual(record.status, StaticPublishTarget.Status.FAILED)
        self.assertEqual(record.error_code, "STATIC_RENDER_FAILED")

    def test_manifest_is_immutable_except_for_active_marker(self):
        _job, manifest = self.build()
        original_files = list(manifest.files)
        manifest.files = []
        with self.assertRaisesMessage(ValidationError, "immutable"):
            manifest.save()
        with self.assertRaisesMessage(ValidationError, "immutable"):
            StaticManifest.objects.filter(pk=manifest.pk).update(metadata={})

        manifest.refresh_from_db()
        self.assertEqual(manifest.files, original_files)
        manifest.is_active = False
        manifest.save(update_fields=("is_active",))
        self.assertFalse(StaticManifest.objects.get(pk=manifest.pk).is_active)

    def test_success_audit_failure_restores_previous_release(self):
        first, _ = self.build()
        current_before = Path(
            self.temporary.name, "current", "manifest.json"
        ).read_bytes()
        TARGETS["/"] = b"<html>home v2</html>"
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
        )

        def fail_success_audit(**kwargs):
            if kwargs["status"] == AuditStatus.SUCCESS:
                raise RuntimeError("audit storage unavailable")
            return persist_audit_event(**kwargs)

        with (
            patch(
                "ai_author_forum.static_publish.services.record_audit_event",
                side_effect=fail_success_audit,
            ),
            self.assertRaisesMessage(AuditWriteError, "Audit log write failed"),
        ):
            self.publisher.build(job)

        job.refresh_from_db()
        self.assertEqual(job.status, StaticPublishJob.Status.FAILED)
        self.assertIn("audit storage unavailable", job.error)
        self.assertEqual(
            Path(self.temporary.name, "current", "manifest.json").read_bytes(),
            current_before,
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, first.version
        )

    def test_tampered_release_is_refused_without_switching_current(self):
        first, _ = self.build()
        TARGETS["/"] = b"<html>home v2</html>"
        second, _ = self.build()
        current_before = Path(
            self.temporary.name, "current", "manifest.json"
        ).read_bytes()
        Path(self.temporary.name, "releases", first.version, "index.html").write_bytes(
            b"tampered"
        )

        with self.assertRaisesMessage(PublishError, "integrity check failed"):
            self.publisher.rollback(
                first.version, self.admin, reason="rollback regression fixture"
            )

        self.assertEqual(
            Path(self.temporary.name, "current", "manifest.json").read_bytes(),
            current_before,
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, second.version
        )
        rollback_job = StaticPublishJob.objects.filter(
            scope=StaticPublishJob.Scope.ROLLBACK
        ).latest("pk")
        self.assertEqual(rollback_job.status, StaticPublishJob.Status.FAILED)

    def test_malformed_release_manifest_is_refused_as_publish_error(self):
        first, _ = self.build()
        TARGETS["/"] = b"<html>home v2</html>"
        second, _ = self.build()
        current_before = Path(
            self.temporary.name, "current", "manifest.json"
        ).read_bytes()
        Path(
            self.temporary.name, "releases", first.version, "manifest.json"
        ).write_text("{not-json", encoding="utf-8")

        with self.assertRaisesMessage(PublishError, "invalid manifest.json"):
            self.publisher.rollback(
                first.version, self.admin, reason="rollback regression fixture"
            )

        self.assertEqual(
            Path(self.temporary.name, "current", "manifest.json").read_bytes(),
            current_before,
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, second.version
        )
        rollback_job = StaticPublishJob.objects.filter(
            scope=StaticPublishJob.Scope.ROLLBACK
        ).latest("pk")
        self.assertEqual(rollback_job.status, StaticPublishJob.Status.FAILED)

    def test_rollback_success_audit_failure_restores_pre_rollback_release(self):
        first, _ = self.build()
        TARGETS["/"] = b"<html>home v2</html>"
        second, _ = self.build()
        current_before = Path(
            self.temporary.name, "current", "manifest.json"
        ).read_bytes()

        def fail_rollback_success(**kwargs):
            if (
                kwargs["action"] == AuditAction.ROLLBACK
                and kwargs["status"] == AuditStatus.SUCCESS
            ):
                raise RuntimeError("rollback audit storage unavailable")
            return persist_audit_event(**kwargs)

        with (
            patch(
                "ai_author_forum.static_publish.services.record_audit_event",
                side_effect=fail_rollback_success,
            ),
            self.assertRaisesMessage(AuditWriteError, "Audit log write failed"),
        ):
            self.publisher.rollback(
                first.version, self.admin, reason="rollback regression fixture"
            )

        self.assertEqual(
            Path(self.temporary.name, "current", "manifest.json").read_bytes(),
            current_before,
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, second.version
        )
        rollback_job = StaticPublishJob.objects.filter(
            scope=StaticPublishJob.Scope.ROLLBACK
        ).latest("pk")
        self.assertEqual(rollback_job.status, StaticPublishJob.Status.FAILED)

    def test_manifest_records_static_page_media_references(self):
        with TemporaryDirectory() as media_root:
            media_file = Path(media_root, "images", "cover.jpg")
            media_file.parent.mkdir(parents=True)
            media_file.write_bytes(b"image")
            TARGETS["/"] = b'<html><img src="/media/images/cover.jpg"></html>'

            with self.settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
                _job, manifest = self.build()

        expected = [{"path": "media/images/cover.jpg", "pages": ["index.html"]}]
        self.assertEqual(manifest.metadata["asset_references"], expected)
        current_data = json.loads(
            Path(self.temporary.name, "current", "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(current_data["asset_references"], expected)

    def test_builds_120_journal_homepages_in_one_release(self):
        TARGETS.clear()
        TARGETS.update(
            {
                f"/journals/journal-{index}/": f"<html>journal {index}</html>".encode()
                for index in range(1, 121)
            }
        )
        job, manifest = self.build()
        self.assertEqual(job.targets.count(), 120)
        self.assertEqual(manifest.metadata["summary"]["pages"], 120)
        self.assertTrue(
            Path(
                self.temporary.name, "current/journals/journal-120/index.html"
            ).is_file()
        )

    def test_selective_build_preserves_unaffected_pages(self):
        first, _ = self.build()
        TARGETS["/"] = b"<html>home v2</html>"
        second, _ = self.build(["/"])
        current = Path(self.temporary.name, "current")
        self.assertEqual((current / "index.html").read_bytes(), TARGETS["/"])
        self.assertTrue((current / "articles/one/index.html").is_file())
        self.assertNotEqual(first.version, second.version)

    def test_selective_build_requires_an_active_release(self):
        with self.assertRaisesMessage(PublishError, "requires an active release"):
            self.build(["/"])
        self.assertEqual(
            StaticPublishJob.objects.get().status, StaticPublishJob.Status.FAILED
        )

    def test_failed_target_does_not_replace_active_release(self):
        first, _ = self.build()
        active_before = Path(self.temporary.name, "current/manifest.json").read_bytes()
        TARGETS["/"] = RuntimeError("template exploded")
        with self.assertRaises(PublishError):
            self.build()
        failed = StaticPublishJob.objects.first()
        self.assertEqual(failed.status, StaticPublishJob.Status.PARTIAL)
        self.assertEqual(
            failed.targets.get(path="index.html").status,
            StaticPublishTarget.Status.FAILED,
        )
        self.assertEqual(
            Path(self.temporary.name, "current/manifest.json").read_bytes(),
            active_before,
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, first.version
        )

    def test_rollback_reactivates_previous_release(self):
        first, _ = self.build()
        TARGETS["/"] = b"<html>home v2</html>"
        self.build()
        rollback = self.publisher.rollback(
            first.version, self.admin, reason="rollback regression fixture"
        )
        self.assertEqual(rollback.status, StaticPublishJob.Status.ROLLED_BACK)
        self.assertEqual(
            Path(self.temporary.name, "current/index.html").read_bytes(),
            b"<html>home v1</html>",
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, first.version
        )

    def test_manifest_database_failure_restores_previous_active_release(self):
        first, _ = self.build()
        active_before = Path(self.temporary.name, "current/manifest.json").read_bytes()
        home_before = Path(self.temporary.name, "current/index.html").read_bytes()
        TARGETS["/"] = b"<html>home v2</html>"
        failed_job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
        )
        with (
            patch.object(
                self.publisher,
                "_record_manifest",
                side_effect=RuntimeError("manifest database failed"),
            ),
            self.assertRaisesMessage(RuntimeError, "manifest database failed"),
        ):
            self.publisher.build(failed_job)
        failed_job.refresh_from_db()
        self.assertEqual(failed_job.status, StaticPublishJob.Status.FAILED)
        self.assertEqual(
            Path(self.temporary.name, "current/manifest.json").read_bytes(),
            active_before,
        )
        self.assertEqual(
            Path(self.temporary.name, "current/index.html").read_bytes(), home_before
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, first.version
        )
        self.assertFalse(Path(self.temporary.name, ".current-previous").exists())

    def test_rollback_database_failure_restores_pre_rollback_release(self):
        first, _ = self.build()
        TARGETS["/"] = b"<html>home v2</html>"
        second, _ = self.build()
        active_before = Path(self.temporary.name, "current/manifest.json").read_bytes()
        home_before = Path(self.temporary.name, "current/index.html").read_bytes()
        with (
            patch(
                "ai_author_forum.static_publish.services.sync_articles_to_active_manifest",
                side_effect=RuntimeError("rollback database failed"),
            ),
            self.assertRaisesMessage(RuntimeError, "rollback database failed"),
        ):
            self.publisher.rollback(
                first.version, self.admin, reason="rollback regression fixture"
            )
        self.assertEqual(
            Path(self.temporary.name, "current/manifest.json").read_bytes(),
            active_before,
        )
        self.assertEqual(
            Path(self.temporary.name, "current/index.html").read_bytes(), home_before
        )
        self.assertEqual(
            StaticManifest.objects.get(is_active=True).version, second.version
        )
        rollback_job = StaticPublishJob.objects.filter(
            scope=StaticPublishJob.Scope.ROLLBACK
        ).latest("pk")
        self.assertEqual(rollback_job.status, StaticPublishJob.Status.FAILED)
        self.assertFalse(Path(self.temporary.name, ".current-previous").exists())

    def test_retry_only_rebuilds_failed_paths(self):
        TARGETS["/"] = RuntimeError("temporary")
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
        )
        with self.assertRaises(PublishError):
            self.publisher.build(job)
        TARGETS["/"] = b"<html>recovered</html>"
        retry = self.publisher.retry(job, self.admin)
        self.assertEqual(retry.retry_of, job)
        self.assertEqual(retry.requested_paths, ["index.html"])
        self.assertEqual(retry.status, StaticPublishJob.Status.SUCCEEDED)

    def test_retry_reuses_requested_paths_after_an_asset_copy_failure(self):
        self.build()
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.SELECTIVE,
            requested_paths=["/articles/one/"],
            triggered_by=self.admin,
        )

        with (
            patch.object(
                self.publisher,
                "_copy_assets",
                side_effect=PermissionError("static asset is unreadable"),
            ),
            self.assertRaises(PermissionError),
        ):
            self.publisher.build(job)

        self.assertTrue(job.targets.exists())
        retry = self.publisher.retry(job, self.admin)

        self.assertEqual(retry.retry_of, job)
        self.assertEqual(retry.requested_paths, ["/articles/one/"])
        self.assertEqual(retry.status, StaticPublishJob.Status.SUCCEEDED)


@override_settings(
    STATIC_PUBLISH_TARGET_PROVIDER=PROVIDER, STATIC_PUBLISH_KEEP_RELEASES=5
)
class ContentReadinessPublishGateTests(TestCase):
    def setUp(self):
        snapshot_patcher = patch.object(
            StaticPublisher, "_configure_snapshot_transaction", return_value=None
        )
        snapshot_patcher.start()
        self.addCleanup(snapshot_patcher.stop)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.publisher = StaticPublisher(self.temporary.name)
        self.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="readiness-publish-admin",
                email="readiness-publish-admin@example.com",
                display_name="Readiness Publish Admin",
                password="test-password",
                is_staff=True,
            )
        )
        TARGETS.clear()
        TARGETS.update({"/": b"<html>blocked</html>"})

    def test_full_publish_blocker_fails_without_activating_release(self):
        finding = ReadinessFinding(
            code="column_minimum_not_met",
            message="Core column is below its configured minimum.",
            target_type="site_settings.contentcolumnconfig",
            target_id="17",
            path="/sections/news/",
        )
        readiness = ContentReadinessResult(
            configured=True,
            blockers=[finding],
            checked_navigation_items=1,
            checked_columns=1,
        )
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
        )

        with (
            patch(
                "ai_author_forum.static_publish.services.requires_content_readiness",
                return_value=True,
            ),
            patch(
                "ai_author_forum.static_publish.services.check_content_readiness",
                return_value=readiness,
            ),
            self.assertRaisesMessage(
                PublishError,
                "Content readiness check failed: "
                "Core column is below its configured minimum.",
            ),
        ):
            self.publisher.build(job)

        job.refresh_from_db()
        self.assertEqual(job.status, StaticPublishJob.Status.FAILED)
        self.assertEqual(
            job.summary["content_readiness"]["blockers"][0]["code"],
            "column_minimum_not_met",
        )
        self.assertFalse(Path(self.temporary.name, "current").exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(job.pk),
            ).exists()
        )


class WorkerPreflightFailureTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.publisher = StaticPublisher(self.temporary.name)
        self.user = get_user_model().objects.create_user(
            "worker-without-permission",
            email="worker-without-permission@example.com",
            display_name="Worker Without Permission",
        )

    def assert_worker_failure(self, job, action):
        job.refresh_from_db()
        self.assertEqual(job.status, StaticPublishJob.Status.FAILED)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(job.error)
        self.assertTrue(
            AuditLog.objects.filter(
                action=action,
                status=AuditStatus.FAILURE,
                target_id=str(job.pk),
                metadata__stage="worker_preflight",
            ).exists()
        )

    def test_retry_permission_failure_is_persisted_and_audited(self):
        failed_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.FULL,
        )
        retry_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.RETRY,
            retry_of=failed_job,
            requested_paths=["articles/retry/index.html"],
            summary={"retry_target_ids": [17]},
            triggered_by=self.user,
        )

        with self.assertRaises(PermissionDenied):
            self.publisher.run_retry(retry_job, self.user)

        self.assert_worker_failure(retry_job, AuditAction.RETRY)
        retry_job.refresh_from_db()
        self.assertEqual(retry_job.summary, {"retry_target_ids": [17]})

    def test_rollback_permission_failure_is_persisted_and_audited(self):
        rollback_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.ROLLBACK,
            version="release-permission-check",
            rollback_version="release-permission-check",
            rollback_reason="worker permission regression",
            triggered_by=self.user,
        )

        with self.assertRaises(PermissionDenied):
            self.publisher.run_rollback(rollback_job, self.user)

        self.assert_worker_failure(rollback_job, AuditAction.ROLLBACK)


class PathSafetyTests(TestCase):
    def test_url_mapping(self):
        self.assertEqual(output_path_for_url("/"), "index.html")
        self.assertEqual(output_path_for_url("/journals/a/"), "journals/a/index.html")

    def test_rejects_parent_traversal(self):
        with self.assertRaises(SuspiciousFileOperation):
            safe_relative_path("../../outside.html")
        with self.assertRaises(SuspiciousFileOperation):
            safe_relative_path("%2e%2e/outside.html")


ARTICLE_PROVIDER = (
    "ai_author_forum.static_publish.tests.providers.TestArticleTargetProvider"
)


@override_settings(
    STATIC_PUBLISH_TARGET_PROVIDER=ARTICLE_PROVIDER,
    STATIC_PUBLISH_KEEP_RELEASES=5,
)
class ArticlePublicationStatusTests(TestCase):
    def setUp(self):
        snapshot_patcher = patch.object(
            StaticPublisher, "_configure_snapshot_transaction", return_value=None
        )
        snapshot_patcher.start()
        self.addCleanup(snapshot_patcher.stop)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.media_temporary = TemporaryDirectory()
        self.addCleanup(self.media_temporary.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_temporary.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.publisher = StaticPublisher(self.temporary.name)
        self.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="article-publication-admin",
                email="article-publication-admin@example.com",
                display_name="Article Publication Admin",
                password="test-password",
                is_staff=True,
            )
        )
        readiness_patcher = patch(
            "ai_author_forum.static_publish.services.check_content_readiness",
            return_value=ContentReadinessResult(),
        )
        readiness_patcher.start()
        self.addCleanup(readiness_patcher.stop)
        ARTICLE_TARGETS.clear()
        self.journal = Journal.objects.create(
            name="Publication Journal",
            slug="publication-journal",
            az_group="P",
        )
        self.article = ArticlePage(
            title="Publication Article",
            slug="publication-article",
            static_slug="publication-article",
            abstract="Publication abstract",
            body=[("paragraph", "<p>Publication body</p>")],
            authors="Publication author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=self.journal,
            keywords="publication",
        )
        Page.get_first_root_node().add_child(instance=self.article)
        self.article.save_revision().publish()
        formally_approve_test_article(self.article, actor=self.admin)
        self.article.refresh_from_db()

    @property
    def article_source(self):
        return f"articles.ArticlePage:{self.article.pk}"

    def build(self):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
        )
        manifest = self.publisher.build(job)
        job.refresh_from_db()
        return job, manifest

    def test_approved_article_becomes_placed_when_effective_placement_exists(self):
        slot = LayoutSlot.objects.get(code="home_featured")
        ArticlePlacement.objects.create(
            article=self.article,
            slot=slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
        )

        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.PLACED,
        )

    def test_successful_build_records_build_and_published_versions(self):
        ARTICLE_TARGETS["/articles/publication-article/"] = (
            b"<html>publication</html>",
            self.article_source,
        )

        job, _ = self.build()

        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.PUBLISHED,
        )
        self.assertEqual(self.article.build_version, job.version)
        self.assertEqual(self.article.published_version, job.version)
        self.assertIsNotNone(self.article.last_built_at)
        self.assertIsNotNone(self.article.last_static_published_at)
        self.assertEqual(self.article.publish_failure_reason, "")

    def test_successful_render_can_be_recorded_as_built_before_activation(self):
        updated = record_article_build_success(self.article_source, "candidate-v1")

        self.assertIsNotNone(updated)
        self.assertEqual(
            updated.publication_status, ArticlePage.PublicationStatus.BUILT
        )
        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.BUILT,
        )
        self.assertEqual(self.article.build_version, "candidate-v1")
        self.assertEqual(self.article.published_version, "")

    def test_failed_article_target_keeps_failure_reason(self):
        ARTICLE_TARGETS["/articles/publication-article/"] = (
            RuntimeError("article template exploded"),
            self.article_source,
        )

        with self.assertRaises(PublishError):
            self.build()

        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.PLACED,
        )
        self.assertIn("article template exploded", self.article.publish_failure_reason)

    def test_rollback_synchronizes_published_and_offline_states(self):
        ARTICLE_TARGETS["/articles/publication-article/"] = (
            b"<html>publication</html>",
            self.article_source,
        )
        first_job, _ = self.build()

        ARTICLE_TARGETS.clear()
        ARTICLE_TARGETS["/"] = (b"<html>home only</html>", "test:home")
        second_job, _ = self.build()
        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.OFFLINE,
        )
        self.assertEqual(self.article.published_version, "")

        self.publisher.rollback(
            first_job.version, self.admin, reason="rollback regression fixture"
        )
        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.PUBLISHED,
        )
        self.assertEqual(self.article.published_version, first_job.version)

        self.publisher.rollback(
            second_job.version, self.admin, reason="rollback regression fixture"
        )
        self.article.refresh_from_db()
        self.assertEqual(
            self.article.publication_status,
            ArticlePage.PublicationStatus.OFFLINE,
        )
        self.assertEqual(self.article.published_version, "")

    def test_canonical_state_is_mirrored_to_legacy_article(self):
        legacy = StaticArticle.objects.create(
            journal=self.journal,
            title="Legacy publication article",
            slug="legacy-publication-article",
            review_status="built",
            build_version="legacy-build-v1",
        )
        self.article.source_static_article = legacy
        self.article.save(
            clean=False,
            bypass_article_permission_check=True,
            update_fields=("source_static_article",),
        )
        ARTICLE_TARGETS["/articles/publication-article/"] = (
            b"<html>publication</html>",
            self.article_source,
        )

        job, _ = self.build()

        legacy.refresh_from_db()
        self.assertEqual(legacy.review_status, "published")
        self.assertEqual(legacy.build_version, job.version)
