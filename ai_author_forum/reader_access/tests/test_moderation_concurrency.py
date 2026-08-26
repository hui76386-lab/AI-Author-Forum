"""PostgreSQL-only acceptance for competing moderation commands."""

from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from django.utils import timezone

from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.reader_interactions.models import Comment, ReaderIdentity

from ..moderation import apply_moderation_command, create_moderation_command


@skipUnless(
    connections["default"].vendor == "postgresql"
    and connections["interactions"].vendor == "postgresql",
    "Moderation concurrency acceptance requires PostgreSQL for both databases.",
)
class ModerationConcurrencyTests(TransactionTestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username=f"moderation-concurrency-{uuid4().hex[:8]}",
            email=f"moderation-{uuid4().hex[:8]}@example.com",
            display_name="Concurrency editor",
            is_staff=True,
        )
        self.journal = Journal.objects.create(
            name=f"Concurrency {uuid4().hex[:8]}",
            slug=f"moderation-{uuid4().hex[:12]}",
            az_group="M",
            status=JournalStatus.ACTIVE,
        )
        JournalEditorAssignment.objects.create(
            user=self.actor,
            journal=self.journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            public_name="Concurrency editor",
            public_role_label="主编",
            created_by=self.actor,
        )
        reader = ReaderIdentity.objects.create(
            email_ciphertext="cipher",
            email_lookup_hmac=uuid4().hex,
            email_key_version=1,
            display_name="Reader",
            email_verified_at=timezone.now(),
        )
        self.comment = Comment.objects.create(
            article_public_id=uuid4(),
            journal_id=self.journal.pk,
            reader=reader,
            body_plaintext="Pending",
            body_sha256=uuid4().hex + uuid4().hex,
            state=Comment.State.PENDING,
        )

    def test_two_commands_same_expected_version_only_one_applies(self):
        command_ids = [
            create_moderation_command(
                actor=self.actor,
                comment_public_id=self.comment.public_id,
                action="approve",
                expected_version=1,
                reason="Review",
                enqueue=False,
            ).command_id
            for _ in range(2)
        ]

        def run(command_id):
            close_old_connections()
            try:
                return apply_moderation_command(command_id).status
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(run, command_ids))
        self.assertEqual(statuses.count("applied"), 1)
        self.assertEqual(statuses.count("failed"), 1)
