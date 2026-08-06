from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ai_author_forum.site_settings.management.commands.seed_roles import (
    ROLE_DEFINITIONS,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.test_helpers import grant_business_super_admin

from ..forms import PublishJobFilterForm
from ..models import StaticManifest, StaticPublishJob, StaticPublishTarget
from ..views import _filtered_jobs


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class PublishCenterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "publisher",
            email="publisher@example.com",
            display_name="Publisher",
            password="secret",
        )
        self.user.is_staff = True
        self.user.save(update_fields=("is_staff",))
        self.user.user_permissions.add(Permission.objects.get(codename="access_admin"))
        self.client.force_login(self.user)

    def grant_permission(self, codename):
        permission = Permission.objects.get(
            content_type__app_label="static_publish", codename=codename
        )
        self.user.user_permissions.add(permission)

    def assert_template_syntax_was_rendered(self, response):
        self.assertNotContains(response, "{%")
        self.assertNotContains(response, "{{")

    def test_center_requires_view_permission(self):
        response = self.client.get(reverse("static_publish:center"))
        self.assertIn(response.status_code, (302, 403))

    def test_viewer_sees_rendered_center_without_publish_controls(self):
        self.grant_permission("view_staticpublishjob")

        response = self.client.get(reverse("static_publish:center"))

        self.assertIn(response.status_code, (302, 403))

    def test_filtered_pagination_preserves_query_parameters(self):
        self.grant_permission("view_staticpublishjob")
        grant_business_super_admin(self.user)
        StaticPublishJob.objects.bulk_create(
            [
                StaticPublishJob(
                    status=StaticPublishJob.Status.FAILED,
                    scope=StaticPublishJob.Scope.FULL,
                    triggered_by=self.user,
                )
                for _ in range(26)
            ]
        )

        response = self.client.get(
            reverse("static_publish:center"),
            {"status": "failed", "scope": "full", "page": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["jobs_page"].number, 2)
        self.assertEqual(
            response.context["jobs_querystring"], "status=failed&scope=full"
        )
        self.assertContains(
            response,
            'href="?status=failed&amp;scope=full&amp;page=1"',
            html=False,
        )

    def test_viewer_cannot_create_job(self):
        self.grant_permission("view_staticpublishjob")

        response = self.client.post(
            reverse("static_publish:center"),
            {"publish-scope": "full", "publish-paths": ""},
        )

        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(StaticPublishJob.objects.count(), 0)

    def test_publisher_sees_rendered_publish_and_rollback_forms(self):
        grant_business_super_admin(self.user)
        self.grant_permission("view_staticpublishjob")
        self.grant_permission("publish_static_site")
        self.grant_permission("publish_category_pages")
        self.grant_permission("rollback_category_publish")

        response = self.client.get(reverse("static_publish:center"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u65b0\u5efa\u53d1\u5e03\u4efb\u52a1")
        self.assertContains(response, "\u786e\u8ba4\u5e76\u8fdb\u5165\u961f\u5217")
        self.assertContains(response, "\u56de\u6eda\u5165\u53e3")
        self.assertContains(
            response,
            f'action="{reverse("static_publish:rollback_preview")}"',
            html=False,
        )
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assert_template_syntax_was_rendered(response)

    def test_job_detail_renders_values_and_hides_retry_from_viewer(self):
        self.grant_permission("view_staticpublishjob")
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.SELECTIVE,
            version="release-20260719",
            error="Rendered failure message",
            triggered_by=self.user,
        )

        response = self.client.get(
            reverse("static_publish:job_detail", kwargs={"job_id": job.pk})
        )

        self.assertIn(response.status_code, (302, 403))

    def test_publisher_sees_rendered_retry_form_for_failed_job(self):
        grant_business_super_admin(self.user)
        self.grant_permission("view_staticpublishjob")
        self.grant_permission("retry_category_publish")
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PARTIAL,
            scope=StaticPublishJob.Scope.FULL,
            version="release-partial",
            triggered_by=self.user,
        )

        response = self.client.get(
            reverse("static_publish:job_detail", kwargs={"job_id": job.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u90e8\u5206\u5931\u8d25")
        self.assertContains(response, "\u5168\u7ad9\u53d1\u5e03")
        self.assertContains(
            response, "\u91cd\u8bd5\u9009\u4e2d\u7684\u5931\u8d25\u76ee\u6807"
        )
        self.assertContains(
            response,
            f'action="{reverse("static_publish:retry_job", kwargs={"job_id": job.pk})}"',
            html=False,
        )
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assert_template_syntax_was_rendered(response)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class PendingPlacementApprovalTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.requester = user_model.objects.create_user(
            "content-requester",
            email="content-requester@example.com",
            display_name="Content Requester",
            password="secret",
        )
        self.publisher = user_model.objects.create_user(
            "approval-publisher",
            email="approval-publisher@example.com",
            display_name="Approval Publisher",
            password="secret",
            is_staff=True,
        )
        self.viewer = user_model.objects.create_user(
            "approval-viewer",
            email="approval-viewer@example.com",
            display_name="Approval Viewer",
            password="secret",
            is_staff=True,
        )
        grant_business_super_admin(self.publisher)
        access_admin = Permission.objects.get(codename="access_admin")
        self.publisher.user_permissions.add(access_admin)
        self.viewer.user_permissions.add(access_admin)
        self.grant_static_permission(self.publisher, "view_staticpublishjob")
        self.grant_static_permission(self.publisher, "publish_static_site")
        self.grant_static_permission(self.publisher, "publish_category_pages")
        self.grant_static_permission(self.viewer, "view_staticpublishjob")

    @staticmethod
    def grant_static_permission(user, codename):
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="static_publish", codename=codename
            )
        )

    def create_pending_approval_job(self, **overrides):
        values = {
            "status": StaticPublishJob.Status.PENDING,
            "scope": StaticPublishJob.Scope.SELECTIVE,
            "requested_paths": ["/"],
            "is_automatic": False,
            "triggered_by": self.requester,
            "summary": {
                "requires_publisher_approval": True,
                "placement_ids": [123],
            },
        }
        values.update(overrides)
        return StaticPublishJob.objects.create(**values)

    def approval_url(self, job):
        return reverse("static_publish:approve_pending_job", kwargs={"job_id": job.pk})

    def test_viewer_cannot_approve_pending_placement_job(self):
        job = self.create_pending_approval_job()
        self.client.force_login(self.viewer)

        with patch(
            "ai_author_forum.static_publish.views.run_static_publish.delay"
        ) as queued:
            response = self.client.post(self.approval_url(job))

        self.assertIn(response.status_code, (302, 403))
        queued.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.triggered_by, self.requester)
        self.assertNotIn("approval_queued_at", job.summary)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH, target_id=str(job.pk)
            ).exists()
        )

    def test_publisher_approval_records_requester_publisher_timestamps_and_audit(self):
        job = self.create_pending_approval_job()
        self.client.force_login(self.publisher)

        with patch(
            "ai_author_forum.static_publish.views.run_static_publish.delay",
            return_value=SimpleNamespace(id="approval-task-1"),
        ) as queued:
            response = self.client.post(self.approval_url(job))

        queued.assert_called_once_with(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.triggered_by, self.publisher)
        self.assertEqual(job.summary["requested_by_id"], self.requester.pk)
        self.assertEqual(job.summary["publisher_approved_by_id"], self.publisher.pk)
        self.assertTrue(job.summary["publisher_approved_at"])
        self.assertTrue(job.summary["approval_queued_at"])
        audit = AuditLog.objects.get(
            action=AuditAction.PUBLISH,
            status=AuditStatus.STARTED,
            target_id=str(job.pk),
            metadata__stage="publisher_approval",
        )
        self.assertEqual(audit.actor, self.publisher)
        self.assertEqual(audit.metadata["requested_by_id"], self.requester.pk)
        self.assertEqual(audit.metadata["paths"], ["/"])
        self.assertEqual(audit.metadata["placement_ids"], [123])
        self.assertRedirects(response, self.approval_url(job).replace("approve/", ""))

    def test_duplicate_approval_does_not_queue_job_twice(self):
        job = self.create_pending_approval_job()
        self.client.force_login(self.publisher)

        with patch(
            "ai_author_forum.static_publish.views.run_static_publish.delay",
            return_value=SimpleNamespace(id="approval-task-duplicate"),
        ) as queued:
            first_response = self.client.post(self.approval_url(job))
            second_response = self.client.post(self.approval_url(job))

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        queued.assert_called_once_with(job.pk)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.STARTED,
                target_id=str(job.pk),
                metadata__stage="publisher_approval",
            ).count(),
            1,
        )

    def test_nonpending_or_nonapproval_job_cannot_be_approved(self):
        jobs = (
            self.create_pending_approval_job(status=StaticPublishJob.Status.FAILED),
            self.create_pending_approval_job(
                summary={"requires_publisher_approval": False}
            ),
        )
        self.client.force_login(self.publisher)

        with patch(
            "ai_author_forum.static_publish.views.run_static_publish.delay"
        ) as queued:
            for job in jobs:
                with self.subTest(job_id=job.pk):
                    response = self.client.post(self.approval_url(job))
                    self.assertEqual(response.status_code, 302)
                    job.refresh_from_db()
                    self.assertEqual(job.triggered_by, self.requester)
                    self.assertNotIn("approval_queued_at", job.summary)

        queued.assert_not_called()
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                target_id__in=[str(job.pk) for job in jobs],
            ).exists()
        )

    def test_queue_failure_marks_approved_job_failed_and_records_failure_audit(self):
        job = self.create_pending_approval_job()
        self.client.force_login(self.publisher)

        with patch(
            "ai_author_forum.static_publish.views.run_static_publish.delay",
            side_effect=RuntimeError("broker unavailable"),
        ):
            response = self.client.post(self.approval_url(job))

        job.refresh_from_db()
        self.assertEqual(job.status, StaticPublishJob.Status.FAILED)
        self.assertEqual(job.triggered_by, self.publisher)
        self.assertIn("broker unavailable", job.error)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.STARTED,
                target_id=str(job.pk),
                metadata__stage="publisher_approval",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(job.pk),
                metadata__stage="queue",
            ).exists()
        )
        self.assertRedirects(
            response,
            reverse("static_publish:job_detail", kwargs={"job_id": job.pk}),
        )


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class SeededPublisherPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.publisher_group = Group.objects.get(
            name=ROLE_DEFINITIONS["super_admin"]["display_name"]
        )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "seeded-publisher",
            email="seeded-publisher@example.com",
            display_name="Seeded Publisher",
            password="secret",
            is_staff=True,
        )
        self.user.groups.add(self.publisher_group)
        self.client.force_login(self.user)

    def test_seeded_publisher_can_queue_publish(self):
        with patch(
            "ai_author_forum.static_publish.views.run_static_publish.delay",
            return_value=SimpleNamespace(id="task-1"),
        ):
            response = self.client.post(
                reverse("static_publish:center"),
                {"publish-scope": "full", "publish-paths": ""},
            )

        job = StaticPublishJob.objects.get(scope=StaticPublishJob.Scope.FULL)
        self.assertRedirects(
            response,
            reverse("static_publish:job_detail", kwargs={"job_id": job.pk}),
        )
        self.assertEqual(job.triggered_by, self.user)

    def test_seeded_publisher_can_retry_failed_job(self):
        failed_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.FULL,
            triggered_by=self.user,
        )
        failed_target = StaticPublishTarget.objects.create(
            job=failed_job,
            path="articles/failed/index.html",
            status=StaticPublishTarget.Status.FAILED,
        )
        with patch(
            "ai_author_forum.static_publish.views.retry_static_publish.delay",
            return_value=SimpleNamespace(id="retry-task-1"),
        ) as retry:
            response = self.client.post(
                reverse(
                    "static_publish:retry_job",
                    kwargs={"job_id": failed_job.pk},
                )
            )

        retry_job = StaticPublishJob.objects.get(retry_of=failed_job)
        retry.assert_called_once_with(retry_job.pk, self.user.pk)
        self.assertEqual(retry_job.status, StaticPublishJob.Status.PENDING)
        self.assertEqual(retry_job.scope, StaticPublishJob.Scope.RETRY)
        self.assertEqual(retry_job.requested_paths, [failed_target.path])
        self.assertEqual(retry_job.summary["retry_target_ids"], [failed_target.pk])
        self.assertEqual(retry_job.triggered_by, self.user)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.RETRY,
                status=AuditStatus.STARTED,
                target_id=str(retry_job.pk),
                metadata__stage="queued",
            ).exists()
        )
        self.assertRedirects(
            response,
            reverse("static_publish:job_detail", kwargs={"job_id": retry_job.pk}),
        )

    def test_retry_rejects_invalid_target_ids_without_creating_job(self):
        failed_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.FULL,
            triggered_by=self.user,
        )
        StaticPublishTarget.objects.create(
            job=failed_job,
            path="articles/invalid-target/index.html",
            status=StaticPublishTarget.Status.FAILED,
        )

        detail_url = reverse(
            "static_publish:job_detail", kwargs={"job_id": failed_job.pk}
        )
        for invalid_target_id in ("abc", "0", "-1", str(2**63)):
            with self.subTest(target_id=invalid_target_id):
                response = self.client.post(
                    reverse(
                        "static_publish:retry_job",
                        kwargs={"job_id": failed_job.pk},
                    ),
                    {"target_ids": [invalid_target_id]},
                    follow=True,
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.redirect_chain, [(detail_url, 302)])
                self.assertContains(response, "选择的重试目标无效，请重新选择。")
                self.assertFalse(
                    StaticPublishJob.objects.filter(retry_of=failed_job).exists()
                )
        failed_job.refresh_from_db()
        self.assertEqual(failed_job.status, StaticPublishJob.Status.FAILED)

    def test_retry_queue_failure_persists_failed_child_and_audit(self):
        failed_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            scope=StaticPublishJob.Scope.FULL,
            triggered_by=self.user,
        )
        StaticPublishTarget.objects.create(
            job=failed_job,
            path="articles/queue-failure/index.html",
            status=StaticPublishTarget.Status.FAILED,
        )
        with patch(
            "ai_author_forum.static_publish.views.retry_static_publish.delay",
            side_effect=RuntimeError("broker unavailable"),
        ):
            response = self.client.post(
                reverse(
                    "static_publish:retry_job",
                    kwargs={"job_id": failed_job.pk},
                )
            )

        retry_job = StaticPublishJob.objects.get(retry_of=failed_job)
        self.assertEqual(retry_job.status, StaticPublishJob.Status.FAILED)
        self.assertIn("broker unavailable", retry_job.error)
        self.assertIsNotNone(retry_job.finished_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.RETRY,
                status=AuditStatus.FAILURE,
                target_id=str(retry_job.pk),
                metadata__stage="queue",
            ).exists()
        )
        self.assertRedirects(
            response,
            reverse("static_publish:job_detail", kwargs={"job_id": retry_job.pk}),
        )

    def test_seeded_publisher_can_rollback_release(self):
        release_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            scope=StaticPublishJob.Scope.FULL,
            version="release-v1",
            triggered_by=self.user,
        )
        manifest = StaticManifest.objects.create(
            version=release_job.version,
            job=release_job,
            files=[],
            is_active=True,
        )
        with patch(
            "ai_author_forum.static_publish.views.rollback_static_publish.delay",
            return_value=SimpleNamespace(id="rollback-task-1"),
        ) as rollback:
            response = self.client.post(
                reverse("static_publish:rollback"),
                {
                    "rollback-version": manifest.pk,
                    "rollback-reason": "rollback permission fixture",
                },
            )

        rollback_job = StaticPublishJob.objects.get(
            scope=StaticPublishJob.Scope.ROLLBACK
        )
        rollback.assert_called_once_with(rollback_job.pk, self.user.pk)
        self.assertEqual(rollback_job.status, StaticPublishJob.Status.PENDING)
        self.assertEqual(rollback_job.rollback_version, release_job.version)
        self.assertEqual(rollback_job.rollback_reason, "rollback permission fixture")
        self.assertEqual(rollback_job.triggered_by, self.user)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.ROLLBACK,
                status=AuditStatus.STARTED,
                target_id=str(rollback_job.pk),
                metadata__stage="queued",
            ).exists()
        )
        self.assertRedirects(
            response,
            reverse("static_publish:job_detail", kwargs={"job_id": rollback_job.pk}),
        )

    def test_rollback_queue_failure_persists_failed_job_and_audit(self):
        release_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            scope=StaticPublishJob.Scope.FULL,
            version="release-queue-failure",
            triggered_by=self.user,
        )
        manifest = StaticManifest.objects.create(
            version=release_job.version,
            job=release_job,
            files=[],
            is_active=True,
        )
        with patch(
            "ai_author_forum.static_publish.views.rollback_static_publish.delay",
            side_effect=RuntimeError("broker unavailable"),
        ):
            response = self.client.post(
                reverse("static_publish:rollback"),
                {
                    "rollback-version": manifest.pk,
                    "rollback-reason": "rollback queue failure fixture",
                },
            )

        rollback_job = StaticPublishJob.objects.get(
            scope=StaticPublishJob.Scope.ROLLBACK
        )
        self.assertEqual(rollback_job.status, StaticPublishJob.Status.FAILED)
        self.assertIn("broker unavailable", rollback_job.error)
        self.assertIsNotNone(rollback_job.finished_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.ROLLBACK,
                status=AuditStatus.FAILURE,
                target_id=str(rollback_job.pk),
                metadata__stage="queue",
            ).exists()
        )
        self.assertRedirects(
            response,
            reverse("static_publish:job_detail", kwargs={"job_id": rollback_job.pk}),
        )


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class SeededReadonlyPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.readonly_group = Group.objects.create(name="Static test technical access")

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "seeded-readonly",
            email="seeded-readonly@example.com",
            display_name="Seeded Readonly",
            password="secret",
            is_staff=True,
        )
        self.user.groups.add(self.readonly_group)
        self.client.force_login(self.user)

    def test_seeded_readonly_cannot_view_publish_center(self):
        response = self.client.get(reverse("static_publish:center"))

        self.assertIn(response.status_code, (302, 403))

    def test_seeded_readonly_cannot_queue_publish(self):
        response = self.client.post(
            reverse("static_publish:center"),
            {"publish-scope": "full", "publish-paths": ""},
        )

        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(StaticPublishJob.objects.exists())


class PublishJobFilterTests(TestCase):
    def setUp(self):
        self.active_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            version="v-active",
        )
        self.rollback_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            version="v-rollback",
        )
        self.failed_target_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PARTIAL,
            version="v-partial",
        )
        self.unmatched_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            version="v-failed",
        )
        StaticManifest.objects.create(
            version="v-active",
            job=self.active_job,
            is_active=True,
        )
        StaticManifest.objects.create(
            version="v-rollback",
            job=self.rollback_job,
            is_active=False,
        )
        StaticPublishTarget.objects.create(
            job=self.failed_target_job,
            path="failed.html",
            status=StaticPublishTarget.Status.FAILED,
        )
        StaticPublishTarget.objects.create(
            job=self.unmatched_job,
            path="ok.html",
            status=StaticPublishTarget.Status.SUCCEEDED,
        )

    def filtered_ids(self, **params):
        form = PublishJobFilterForm(params)
        self.assertTrue(form.is_valid(), form.errors)
        return set(_filtered_jobs(form).values_list("pk", flat=True))

    def test_filter_form_exposes_complete_chinese_labels_and_choices(self):
        form = PublishJobFilterForm()

        self.assertEqual(
            {name: field.label for name, field in form.fields.items()},
            {
                "status": "\u72b6\u6001",
                "scope": "\u8303\u56f4",
                "target_status": "\u76ee\u6807\u72b6\u6001",
                "manifest_status": "\u7248\u672c\u72b6\u6001",
                "triggered_by": "\u53d1\u8d77\u4eba",
                "created_from": "\u5f00\u59cb\u65e5\u671f",
                "created_to": "\u7ed3\u675f\u65e5\u671f",
            },
        )
        self.assertEqual(
            form.fields["status"].choices[0], ("", "\u5168\u90e8\u72b6\u6001")
        )
        self.assertEqual(
            form.fields["scope"].choices[0], ("", "\u5168\u90e8\u8303\u56f4")
        )
        self.assertEqual(
            form.fields["target_status"].choices[0],
            ("", "\u5168\u90e8\u76ee\u6807\u72b6\u6001"),
        )
        self.assertIn(
            ("active", "\u5f53\u524d\u6d3b\u52a8\u7248\u672c"),
            form.fields["manifest_status"].choices,
        )
        self.assertIn(
            ("rollback", "\u53ef\u56de\u6eda\u7248\u672c"),
            form.fields["manifest_status"].choices,
        )

    def test_target_status_filters_jobs_without_duplicate_rows(self):
        StaticPublishTarget.objects.create(
            job=self.failed_target_job,
            path="failed-again.html",
            status=StaticPublishTarget.Status.FAILED,
        )

        self.assertEqual(
            self.filtered_ids(target_status=StaticPublishTarget.Status.FAILED),
            {self.failed_target_job.pk},
        )

    def test_manifest_status_filters_active_and_rollback_jobs(self):
        self.assertEqual(
            self.filtered_ids(manifest_status="active"),
            {self.active_job.pk},
        )
        self.assertEqual(
            self.filtered_ids(manifest_status="rollback"),
            {self.rollback_job.pk},
        )

    def test_created_from_and_status_apply_dashboard_contract(self):
        old_job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.FAILED,
            version="v-old",
        )
        StaticPublishJob.objects.filter(pk=old_job.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        today = timezone.localdate().isoformat()

        self.assertEqual(
            self.filtered_ids(
                status=StaticPublishJob.Status.FAILED,
                created_from=today,
            ),
            {self.unmatched_job.pk},
        )
