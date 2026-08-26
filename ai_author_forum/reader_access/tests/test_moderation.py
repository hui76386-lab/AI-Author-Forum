from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.reader_interactions.models import (
    ArticleCapabilityProjection,
    Comment,
    CommentModerationEvent,
    CommentReport,
    ReaderIdentity,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from ..models import ModerationCommand
from ..moderation import (
    apply_moderation_command,
    batch_moderate_comments,
    create_moderation_command,
    reconcile_moderation_commands,
)


class ModerationServiceTests(TestCase):
    databases = {"default", "interactions"}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="moderation-chief",
            email="moderation-chief@example.com",
            display_name="Chief",
            is_staff=True,
        )
        cls.journal = Journal.objects.create(
            name="Moderation Journal",
            slug="moderation-journal",
            az_group="M",
            status=JournalStatus.ACTIVE,
        )
        JournalEditorAssignment.objects.create(
            user=cls.user,
            journal=cls.journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            responsibilities=[],
            public_name="Chief",
            public_role_label="主编",
            created_by=cls.user,
        )

    def setUp(self):
        self.reader = ReaderIdentity.objects.create(
            email_ciphertext="cipher",
            email_lookup_hmac=uuid4().hex,
            email_key_version=1,
            display_name="Reader",
            email_verified_at=timezone.now(),
        )
        self.article_id = uuid4()
        ArticleCapabilityProjection.objects.create(
            article_public_id=self.article_id,
            journal_id=self.journal.pk,
            active_release="release-1",
            approved_revision_id=1,
            comments_mode="open",
            download_enabled=False,
            policy_version=1,
            projection_version=1,
            applied_at=timezone.now(),
        )
        self.comment = Comment.objects.create(
            article_public_id=self.article_id,
            journal_id=self.journal.pk,
            reader=self.reader,
            body_plaintext="Needs review",
            body_sha256="a" * 64,
            state=Comment.State.PENDING,
        )

    def test_expected_version_transition_is_idempotent_and_audited(self):
        report = CommentReport.objects.create(
            comment=self.comment,
            reporter=self.reader,
            reason=CommentReport.Reason.OTHER,
        )
        first = create_moderation_command(
            actor=self.user,
            comment_public_id=self.comment.public_id,
            action="approve",
            expected_version=1,
            reason="Editorial review",
            idempotency_key="same-command",
            enqueue=False,
        )
        replay = create_moderation_command(
            actor=self.user,
            comment_public_id=self.comment.public_id,
            action="approve",
            expected_version=1,
            reason="Editorial review",
            idempotency_key="same-command",
            enqueue=False,
        )
        self.assertEqual(first.command_id, replay.command_id)
        result = apply_moderation_command(first.command_id)
        self.assertEqual(result.status, ModerationCommand.Status.APPLIED)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.state, Comment.State.PUBLISHED)
        self.assertEqual(CommentModerationEvent.objects.count(), 1)
        report.refresh_from_db()
        self.assertEqual(report.status, CommentReport.Status.DISMISSED)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.MODERATION)
            .values_list("status", flat=True)
            .count(),
            2,
        )
        success = AuditLog.objects.get(
            action=AuditAction.MODERATION, status=AuditStatus.SUCCESS
        )
        self.assertEqual(success.metadata["expected_version"], 1)
        self.assertEqual(success.metadata["actual_version"], 2)
        self.assertEqual(success.metadata["from_state"], Comment.State.PENDING)
        self.assertEqual(success.metadata["to_state"], Comment.State.PUBLISHED)
        self.assertEqual(success.metadata["release_version"], "release-1")
        self.assertNotIn("Needs review", str(success.metadata))

    def test_idempotency_payload_conflict_and_stale_version_fail_closed(self):
        create_moderation_command(
            actor=self.user,
            comment_public_id=self.comment.public_id,
            action="approve",
            expected_version=1,
            reason="original",
            idempotency_key="same-command",
            enqueue=False,
        )
        with self.assertRaises(ValidationError):
            create_moderation_command(
                actor=self.user,
                comment_public_id=self.comment.public_id,
                action="approve",
                expected_version=1,
                reason="different",
                idempotency_key="same-command",
                enqueue=False,
            )
        command = create_moderation_command(
            actor=self.user,
            comment_public_id=self.comment.public_id,
            action="approve",
            expected_version=9,
            reason="Editorial review",
            enqueue=False,
        )
        result = apply_moderation_command(command.command_id)
        self.assertEqual(result.status, ModerationCommand.Status.FAILED)
        self.assertEqual(result.body["error_code"], "stale_version")
        self.assertFalse(CommentModerationEvent.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.MODERATION, status=AuditStatus.FAILURE
            ).exists()
        )

    def test_unknown_result_never_reports_success_and_can_reconcile(self):
        command = create_moderation_command(
            actor=self.user,
            comment_public_id=self.comment.public_id,
            action="approve",
            expected_version=1,
            reason="Editorial review",
            enqueue=False,
        )
        with patch(
            "ai_author_forum.reader_access.moderation._apply_interaction_command",
            side_effect=RuntimeError("connection lost"),
        ):
            result = apply_moderation_command(command.command_id)
        self.assertEqual(result.status, ModerationCommand.Status.UNKNOWN)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditAction.MODERATION, status=AuditStatus.SUCCESS
            ).exists()
        )
        event = CommentModerationEvent.objects.create(
            comment=self.comment,
            from_state=Comment.State.PENDING,
            to_state=Comment.State.PUBLISHED,
            action="approve",
            actor_type=CommentModerationEvent.ActorType.EDITOR,
            actor_id=str(self.user.pk),
            command_id=command.command_id,
        )
        report = reconcile_moderation_commands()
        self.assertEqual(report["reconciled"], 1)
        command_record = ModerationCommand.objects.get(command_id=command.command_id)
        self.assertEqual(command_record.status, ModerationCommand.Status.APPLIED)
        self.assertEqual(command_record.result_body["event_id"], str(event.event_id))

    def test_batch_is_partial_and_scope_is_enforced(self):
        rows = batch_moderate_comments(
            actor=self.user,
            items=[
                {
                    "comment_public_id": self.comment.public_id,
                    "action": "approve",
                    "expected_version": 1,
                    "reason": "Reviewed",
                    "enqueue": False,
                },
                {
                    "comment_public_id": uuid4(),
                    "action": "approve",
                    "expected_version": 1,
                    "reason": "Reviewed",
                    "enqueue": False,
                },
            ],
        )
        self.assertEqual(rows[0]["status"], ModerationCommand.Status.PENDING)
        self.assertEqual(rows[1]["status"], "failed")
        other_user = get_user_model().objects.create_user(
            username="moderation-outsider", email="outsider@example.com"
        )
        with self.assertRaises(PermissionDenied):
            create_moderation_command(
                actor=other_user,
                comment_public_id=self.comment.public_id,
                action="hide",
                expected_version=1,
                reason="No scope",
                enqueue=False,
            )
