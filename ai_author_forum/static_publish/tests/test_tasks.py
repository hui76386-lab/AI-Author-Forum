from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from ..models import StaticPublishJob
from ..tasks import retry_static_publish, rollback_static_publish, run_static_publish


class StaticPublishTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("task-publisher")

    @patch("ai_author_forum.static_publish.tasks.StaticPublisher")
    def test_run_static_publish_builds_existing_job(self, publisher_class):
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.FULL,
            version="release-full",
        )
        publisher_class.return_value.build.return_value = object()

        result = run_static_publish.run(job.pk)

        publisher_class.return_value.build.assert_called_once_with(job)
        self.assertEqual(
            result,
            {
                "job_id": job.pk,
                "status": StaticPublishJob.Status.PENDING,
                "version": "release-full",
            },
        )

    @patch("ai_author_forum.static_publish.tasks.StaticPublisher")
    def test_retry_static_publish_executes_existing_child_job(self, publisher_class):
        failed_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.FULL,
        )
        retry_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.RETRY,
            version="release-retry",
            retry_of=failed_job,
            triggered_by=self.user,
        )

        result = retry_static_publish.run(retry_job.pk, self.user.pk)

        publisher_class.return_value.run_retry.assert_called_once_with(
            retry_job, self.user
        )
        publisher_class.return_value.retry.assert_not_called()
        self.assertEqual(
            result,
            {
                "job_id": retry_job.pk,
                "status": StaticPublishJob.Status.PENDING,
                "version": "release-retry",
            },
        )

    @patch("ai_author_forum.static_publish.tasks.StaticPublisher")
    def test_rollback_static_publish_executes_existing_job(self, publisher_class):
        rollback_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.ROLLBACK,
            version="release-rollback",
            rollback_version="release-original",
            rollback_reason="rollback regression fixture",
            triggered_by=self.user,
        )

        result = rollback_static_publish.run(rollback_job.pk, self.user.pk)

        publisher_class.return_value.run_rollback.assert_called_once_with(
            rollback_job, self.user
        )
        publisher_class.return_value.rollback.assert_not_called()
        self.assertEqual(
            result,
            {
                "job_id": rollback_job.pk,
                "status": StaticPublishJob.Status.PENDING,
                "version": "release-rollback",
            },
        )

    def test_retry_task_marks_deleted_actor_as_worker_preflight_failure(self):
        failed_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.FULL,
        )
        retry_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.RETRY,
            retry_of=failed_job,
            requested_paths=["articles/deleted-actor/index.html"],
            triggered_by=self.user,
        )
        deleted_user_id = self.user.pk
        self.user.delete()

        with self.assertRaisesMessage(PermissionDenied, "发起操作的用户不存在"):
            retry_static_publish.run(retry_job.pk, deleted_user_id)

        retry_job.refresh_from_db()
        self.assertEqual(retry_job.status, StaticPublishJob.Status.FAILED)
        self.assertIsNotNone(retry_job.finished_at)
        self.assertTrue(retry_job.error)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.RETRY,
                status=AuditStatus.FAILURE,
                target_id=str(retry_job.pk),
                metadata__stage="worker_preflight",
                metadata__requested_user_id=deleted_user_id,
            ).exists()
        )
