from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from ai_author_forum.users.models import User
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from ..models import StaticPublishJob
from ..services import create_publish_job, create_retry_job, create_rollback_job


class SimpleRbacStaticPublishAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        user_model = get_user_model()
        cls.admin = user_model.objects.create_user(
            username="static-business-admin",
            email="static-business-admin@example.com",
            display_name="Static Business Admin",
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.django_superuser = user_model.objects.create_superuser(
            username="static-django-superuser",
            email="static-django-superuser@example.com",
            display_name="Static Django Superuser",
            password="Static-test-password-2026!",
        )

    def test_only_business_super_admin_can_create_publish_retry_and_rollback_jobs(self):
        publish_job = create_publish_job(
            scope=StaticPublishJob.Scope.FULL,
            paths=[],
            actor=self.admin,
        )
        failed_job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            status=StaticPublishJob.Status.FAILED,
            triggered_by=self.admin,
        )
        retry_job = create_retry_job(
            failed_job=failed_job,
            actor=self.admin,
            paths=[],
            scope=StaticPublishJob.Scope.FULL,
        )
        rollback_job = create_rollback_job(
            version="release-rbac-accepted",
            actor=self.admin,
            reason="Restore the last verified release.",
        )
        self.assertEqual(publish_job.triggered_by, self.admin)
        self.assertEqual(retry_job.triggered_by, self.admin)
        self.assertEqual(rollback_job.triggered_by, self.admin)

        for operation in (
            lambda: create_publish_job(
                scope=StaticPublishJob.Scope.FULL,
                paths=[],
                actor=self.django_superuser,
            ),
            lambda: create_retry_job(
                failed_job=failed_job,
                actor=self.django_superuser,
                paths=[],
                scope=StaticPublishJob.Scope.FULL,
            ),
            lambda: create_rollback_job(
                version="release-rbac-denied",
                actor=self.django_superuser,
                reason="This actor has no business role.",
            ),
        ):
            with self.assertRaises(PermissionDenied):
                operation()

    def test_revoked_business_admin_is_rejected_when_worker_would_recheck(self):
        self.admin.account_status = User.AccountStatus.SUSPENDED
        self.admin.status_reason = "Role revoked before worker execution."
        self.admin.save(update_fields=("account_status", "status_reason", "is_active"))

        with self.assertRaises(PermissionDenied):
            create_publish_job(
                scope=StaticPublishJob.Scope.FULL,
                paths=[],
                actor=self.admin,
            )

    def test_publish_center_denies_django_superuser_without_business_role(self):
        self.client.force_login(self.django_superuser)
        response = self.client.get(reverse("static_publish:center"))
        self.assertIn(response.status_code, (302, 403))
        if response.status_code == 302:
            self.assertEqual(response.headers["Location"], reverse("wagtailadmin_home"))

        self.client.force_login(self.admin)
        response = self.client.get(reverse("static_publish:center"))
        self.assertEqual(response.status_code, 200)
