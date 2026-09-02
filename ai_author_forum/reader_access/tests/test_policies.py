import json
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.reader_interactions.capabilities import (
    ProjectionConflict,
    apply_capability_projection,
)
from ai_author_forum.reader_interactions.models import ArticleCapabilityProjection
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob
from ai_author_forum.test_helpers import grant_business_super_admin

from ..models import (
    ArticleInteractionPolicy,
    ControlPlaneOutbox,
)
from ..permissions import can_manage_policy
from ..services import (
    StalePolicy,
    effective_policy,
    update_article_policy,
    update_journal_policy,
)
from ..tasks import apply_capability_projection as projection_task
from ..wagtail_hooks import article_policy_edit


class MemoryDenyStore:
    values = {}

    def set_many_desired(self, payloads):
        for payload in payloads:
            self.values[str(payload["article_public_id"])] = dict(payload)
        return list(self.values.items())

    def get_desired(self, article_public_id):
        return self.values.get(str(article_public_id))

    def clear_if_matches(self, article_public_id, payload):
        current = self.values.get(str(article_public_id))
        expected = dict(payload)
        if current == expected:
            self.values.pop(str(article_public_id), None)
            return 1
        return 0


class ReaderPolicyTests(TestCase):
    databases = {"default", "interactions"}

    @classmethod
    def setUpTestData(cls):
        admin_access = Permission.objects.get(
            content_type__app_label="wagtailadmin", codename="access_admin"
        )
        cls.journal = Journal.objects.create(
            name="Reader Policy Journal",
            slug="reader-policy-journal",
            az_group="R",
            status=JournalStatus.ACTIVE,
        )
        cls.other_journal = Journal.objects.create(
            name="Other Policy Journal",
            slug="other-policy-journal",
            az_group="O",
            status=JournalStatus.ACTIVE,
        )
        cls.users = {}
        for role in JournalEditorAssignment.Role.values:
            user = get_user_model().objects.create_user(
                username=f"reader-{role}",
                email=f"reader-{role}@example.com",
                display_name=role,
                is_staff=True,
                password="test-password",
            )
            JournalEditorAssignment.objects.create(
                user=user,
                journal=cls.journal,
                role=role,
                responsibilities=(
                    [JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT]
                    if role == JournalEditorAssignment.Role.ASSOCIATE_EDITOR
                    else []
                ),
                public_name=role,
                public_role_label=JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[
                    role
                ],
                created_by=user,
            )
            user.user_permissions.add(admin_access)
            cls.users[role] = user
        cls.outsider = get_user_model().objects.create_user(
            username="reader-policy-outsider",
            email="reader-policy-outsider@example.com",
            display_name="Outsider",
            is_staff=True,
            is_superuser=True,
            password="test-password",
        )
        cls.outsider.user_permissions.add(admin_access)
        Group.objects.create(name="Legacy Reader Policy Editors").user_set.add(
            cls.outsider
        )
        cls.platform_admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="reader-policy-admin",
                email="reader-policy-admin@example.com",
                display_name="Reader Policy Admin",
                is_staff=True,
                password="test-password",
            )
        )
        cls.article = ArticlePage(
            title="Reader policy article",
            slug="reader-policy-article",
            static_slug="reader-policy-article",
            abstract="Reader policy abstract",
            body=[("paragraph", "Reader policy body")],
            authors="Reader Author",
            keywords="reader",
            responsibility_statement="Authors retain responsibility.",
            primary_journal=cls.journal,
        )
        Page.get_first_root_node().add_child(instance=cls.article)

    def setUp(self):
        MemoryDenyStore.values = {}
        self.store_patch = patch(
            "ai_author_forum.reader_access.services.CapabilityDenyStore",
            return_value=MemoryDenyStore(),
        )
        self.queue_patch = patch(
            "ai_author_forum.reader_access.services._enqueue_projection"
        )
        self.store_patch.start()
        self.queue_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.queue_patch.stop)

    def test_all_three_effective_roles_can_manage_without_responsibility(self):
        for user in self.users.values():
            self.assertTrue(can_manage_policy(user, self.journal))
        self.assertFalse(can_manage_policy(self.outsider, self.journal))
        self.assertFalse(
            can_manage_policy(self.users["chief_editor"], self.other_journal)
        )

    def test_expired_assignment_old_group_and_is_superuser_do_not_bypass(self):
        assignment = JournalEditorAssignment.objects.get(
            user=self.users["associate_editor"], journal=self.journal
        )
        JournalEditorAssignment.objects.filter(pk=assignment.pk).update(is_active=False)
        self.assertFalse(
            can_manage_policy(self.users["associate_editor"], self.journal)
        )
        self.assertFalse(can_manage_policy(self.outsider, self.journal))
        with self.assertRaises(PermissionDenied):
            update_article_policy(
                actor=self.outsider,
                article=self.article,
                expected_version=0,
                comments_policy="hidden",
                pdf_download_policy="disabled",
            )

    def test_policy_write_creates_atomic_audit_outbox_and_deny_state(self):
        policy, event_count = update_article_policy(
            actor=self.users["associate_editor"],
            article=self.article,
            expected_version=0,
            comments_policy=ArticleInteractionPolicy.CommentsPolicy.HIDDEN,
            pdf_download_policy=ArticleInteractionPolicy.PdfDownloadPolicy.DISABLED,
        )
        event = ControlPlaneOutbox.objects.get(event_type="reader.capability.desired")
        audit = AuditLog.objects.get(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            target_type="ArticleInteractionPolicy",
        )
        self.assertEqual(policy.version, 1)
        self.assertEqual(event_count, 1)
        self.assertEqual(event.payload["comments_mode"], "hidden")
        self.assertFalse(event.payload["download_enabled"])
        self.assertEqual(audit.metadata["version"], 1)
        self.assertIn(str(self.article.public_id), MemoryDenyStore.values)

    def test_expected_version_and_redis_failure_abort_database_write(self):
        update_article_policy(
            actor=self.users["chief_editor"],
            article=self.article,
            expected_version=0,
            comments_policy="read_only",
            pdf_download_policy="inherit",
        )
        with self.assertRaises(StalePolicy):
            update_article_policy(
                actor=self.users["chief_editor"],
                article=self.article,
                expected_version=0,
                comments_policy="open",
                pdf_download_policy="inherit",
            )
        with (
            patch(
                f"{__name__}.MemoryDenyStore.set_many_desired",
                side_effect=ValidationError("down"),
            ),
            self.assertRaises(ValidationError),
        ):
            update_article_policy(
                actor=self.users["chief_editor"],
                article=self.article,
                expected_version=1,
                comments_policy="open",
                pdf_download_policy="inherit",
            )
        self.assertEqual(
            ArticleInteractionPolicy.objects.get(article=self.article).version, 1
        )
        self.assertEqual(ControlPlaneOutbox.objects.count(), 1)

    def test_policy_outbox_contains_monotonic_projection_version_and_converges(self):
        update_article_policy(
            actor=self.users["chief_editor"],
            article=self.article,
            expected_version=0,
            comments_policy="hidden",
            pdf_download_policy="disabled",
        )
        event = ControlPlaneOutbox.objects.get(event_type="reader.capability.desired")
        self.assertGreater(event.payload["projection_version"], 0)
        self.assertEqual(event.aggregate_version, event.payload["projection_version"])
        with patch(
            "ai_author_forum.reader_access.tasks.CapabilityDenyStore",
            return_value=MemoryDenyStore(),
        ):
            result = projection_task.run(str(event.event_id))
        projection = ArticleCapabilityProjection.objects.get(
            article_public_id=self.article.public_id
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(
            projection.projection_version, event.payload["projection_version"]
        )
        self.assertEqual(projection.comments_mode, "hidden")

    def test_platform_admin_requires_emergency_reason(self):
        with self.assertRaises(PermissionDenied):
            update_journal_policy(
                actor=self.platform_admin,
                journal=self.journal,
                expected_version=0,
                comments_mode="hidden",
                download_enabled=False,
            )
        policy, _count = update_journal_policy(
            actor=self.platform_admin,
            journal=self.journal,
            expected_version=0,
            comments_mode="hidden",
            download_enabled=False,
            reason="Emergency abuse response",
        )
        self.assertEqual(policy.version, 1)

    def test_inactive_article_is_fail_closed_even_when_policy_is_open(self):
        policy = effective_policy(self.article)
        self.assertEqual(policy["comments_mode"], "hidden")
        self.assertFalse(policy["download_enabled"])
        self.assertEqual(policy["active_release"], "")

    def test_policy_admin_repeats_object_scope_checks_on_get_and_post(self):
        self.client.force_login(self.users["associate_editor"])
        index = self.client.get(reverse("reader_access_policy_index"))
        self.assertEqual(index.status_code, 200)
        self.assertContains(index, self.article.title)
        changed = self.client.post(
            reverse("reader_access_article_policy_edit", args=[self.article.pk]),
            {
                "comments_policy": "hidden",
                "pdf_download_policy": "disabled",
                "expected_version": 0,
                "reason": "",
            },
        )
        self.assertRedirects(changed, reverse("reader_access_policy_index"))
        self.assertEqual(
            ArticleInteractionPolicy.objects.get(article=self.article).comments_policy,
            "hidden",
        )

        denied_request = RequestFactory().get(
            reverse("reader_access_article_policy_edit", args=[self.article.pk])
        )
        denied_request.user = self.outsider
        with self.assertRaises(PermissionDenied):
            article_policy_edit(denied_request, self.article.pk)

    def test_active_manifest_enables_comments_but_not_missing_artifact(self):
        revision = self.article.save_revision(
            user=self.users["chief_editor"], bypass_article_permission_check=True
        )
        job = StaticPublishJob.objects.create(status=StaticPublishJob.Status.SUCCEEDED)
        manifest = StaticManifest.objects.create(
            version="ri04-active-release",
            job=job,
            files=[{"path": "articles/reader-policy-article/index.html"}],
            is_active=True,
        )
        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED,
            approved_version_id=revision.pk,
            publication_status=ArticlePage.PublicationStatus.PUBLISHED,
            published_version=manifest.version,
        )
        self.article.refresh_from_db()
        policy = effective_policy(self.article)
        self.assertEqual(policy["comments_mode"], "open")
        self.assertFalse(policy["download_enabled"])


class CapabilityProjectionTests(TestCase):
    databases = {"default", "interactions"}

    def payload(self, version, **changes):
        payload = {
            "article_public_id": str(uuid4()),
            "journal_id": 10,
            "active_release": "release-one",
            "approved_revision_id": 20,
            "comments_mode": "open",
            "download_enabled": False,
            "protected_artifact_public_id": None,
            "policy_version": version,
            "projection_version": version,
        }
        payload.update(changes)
        return payload

    def test_projection_is_idempotent_and_never_moves_backwards(self):
        payload = self.payload(3)
        self.assertEqual(apply_capability_projection(payload)[0], "created")
        self.assertEqual(apply_capability_projection(payload)[0], "duplicate")
        self.assertEqual(
            apply_capability_projection(
                {**payload, "policy_version": 2, "projection_version": 2}
            )[0],
            "stale",
        )
        with self.assertRaises(ProjectionConflict):
            apply_capability_projection({**payload, "comments_mode": "hidden"})
        projection = ArticleCapabilityProjection.objects.get(
            article_public_id=payload["article_public_id"]
        )
        self.assertEqual(projection.projection_version, 3)
        self.assertEqual(projection.comments_mode, "open")

    @override_settings(READER_CAPABILITY_REDIS_URL="redis://unused/1")
    def test_outbox_task_is_at_least_once_and_clears_matching_deny(self):
        payload = self.payload(7)
        event = ControlPlaneOutbox.objects.create(
            event_type="reader.capability.desired",
            aggregate_type="article_capability",
            aggregate_id=payload["article_public_id"],
            aggregate_version=7,
            payload=payload,
        )
        store = MemoryDenyStore()
        store.set_many_desired([payload])
        with patch(
            "ai_author_forum.reader_access.tasks.CapabilityDenyStore",
            return_value=store,
        ):
            first = projection_task.run(str(event.event_id))
            second = projection_task.run(str(event.event_id))
        event.refresh_from_db()
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "ignored")
        self.assertIsNotNone(event.published_at)
        self.assertNotIn(payload["article_public_id"], MemoryDenyStore.values)


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_COMMENTS_WRITE_ENABLED=True,
    READER_PDF_GRANTS_ENABLED=True,
    READER_INTERNAL_SERVICE_TOKEN="internal-service-token-which-is-long-enough",
)
class CapabilityApiTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        MemoryDenyStore.values = {}
        self.article_id = uuid4()
        self.projection = ArticleCapabilityProjection.objects.create(
            article_public_id=self.article_id,
            journal_id=1,
            active_release="release-api",
            approved_revision_id=2,
            comments_mode="open",
            download_enabled=True,
            protected_artifact_public_id=uuid4(),
            policy_version=5,
            projection_version=5,
            applied_at=timezone.now(),
        )

    def test_public_capability_api_is_anonymous_safe_and_fail_closed_on_stale_deny(
        self,
    ):
        store = MemoryDenyStore()
        with patch(
            "ai_author_forum.reader_interactions.capabilities.CapabilityDenyStore",
            return_value=store,
        ):
            open_response = self.client.get(
                reverse("reader_article_capabilities", args=[self.article_id]),
                secure=True,
            )
            store.values[str(self.article_id)] = {
                "active_release": "release-api",
                "approved_revision_id": 2,
                "policy_version": 6,
                "projection_version": 6,
                "comments_mode": "hidden",
                "download_enabled": False,
                "protected_artifact_public_id": "",
            }
            closed_response = self.client.get(
                reverse("reader_article_capabilities", args=[self.article_id]),
                secure=True,
            )
        self.assertEqual(open_response.status_code, 200)
        self.assertEqual(open_response.json()["data"]["comments_mode"], "open")
        self.assertFalse(open_response.json()["data"]["can_comment"])
        self.assertEqual(closed_response.json()["data"]["comments_mode"], "hidden")
        self.assertFalse(closed_response.json()["data"]["pdf_available"])
        self.assertTrue(closed_response.json()["data"]["applying"])

    def test_internal_projection_endpoint_requires_token_and_rejects_id_mismatch(self):
        next_id = uuid4()
        payload = {
            "article_public_id": str(next_id),
            "journal_id": 1,
            "active_release": "release-api",
            "approved_revision_id": 2,
            "comments_mode": "hidden",
            "download_enabled": False,
            "protected_artifact_public_id": None,
            "policy_version": 1,
            "projection_version": 1,
        }
        url = reverse("reader_internal_article_capability", args=[next_id])
        denied = self.client.put(
            url, data=json.dumps(payload), content_type="application/json", secure=True
        )
        accepted = self.client.put(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer internal-service-token-which-is-long-enough",
            secure=True,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(
            ArticleCapabilityProjection.objects.filter(
                article_public_id=next_id
            ).exists()
        )
