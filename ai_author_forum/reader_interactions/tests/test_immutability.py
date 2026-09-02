from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from ..models import (
    Comment,
    CommentModerationEvent,
    CommentSnapshot,
    InteractionOutbox,
    ReaderIdentity,
)


class InteractionImmutabilityTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.reader = ReaderIdentity.objects.create(
            email_ciphertext="ciphertext",
            email_lookup_hmac="a" * 64,
            email_key_version=1,
            email_verified_at=timezone.now(),
            display_name="Reader",
        )
        self.comment = Comment.objects.create(
            article_public_id=uuid4(),
            journal_id=42,
            reader=self.reader,
            body_plaintext="A useful comment.",
            body_sha256="b" * 64,
            request_id=uuid4(),
            published_at=timezone.now(),
        )

    def test_moderation_event_cannot_be_updated_or_deleted(self):
        event = CommentModerationEvent.objects.create(
            comment=self.comment,
            from_state="",
            to_state=Comment.State.PUBLISHED,
            action="created",
            actor_type=CommentModerationEvent.ActorType.READER,
            actor_id=str(self.reader.public_id),
        )

        with self.assertRaises(ValidationError):
            CommentModerationEvent.objects.filter(pk=event.pk).update(note="changed")
        with self.assertRaises(ValidationError):
            event.delete()

    def test_comment_snapshot_is_immutable(self):
        snapshot = CommentSnapshot.objects.create(
            article_public_id=self.comment.article_public_id,
            version=1,
            object_key="comment-snapshots/article/1.json",
            etag='"snapshot-1"',
            comment_count=1,
        )

        snapshot.comment_count = 2
        with self.assertRaises(ValidationError):
            snapshot.save()
        with self.assertRaises(ValidationError):
            CommentSnapshot.objects.filter(pk=snapshot.pk).delete()

    def test_outbox_event_id_is_idempotent_and_delivery_updates_are_controlled(self):
        event_id = uuid4()
        event = InteractionOutbox.objects.create(
            event_id=event_id,
            event_type="comment.created",
            aggregate_type="comment",
            aggregate_id=str(self.comment.public_id),
            aggregate_version=1,
            payload={"comment_id": str(self.comment.public_id)},
        )

        with (
            self.assertRaises(IntegrityError),
            transaction.atomic(using="interactions"),
        ):
            InteractionOutbox.objects.create(
                event_id=event_id,
                event_type="comment.created",
                aggregate_type="comment",
                aggregate_id=str(self.comment.public_id),
                aggregate_version=1,
            )
        with self.assertRaises(ValidationError):
            InteractionOutbox.objects.filter(pk=event.pk).update(attempts=99)

        self.assertEqual(
            InteractionOutbox.objects.record_attempt(event_id, error="temporary"), 1
        )
        self.assertEqual(InteractionOutbox.objects.mark_published(event_id), 1)
        self.assertEqual(InteractionOutbox.objects.mark_published(event_id), 0)
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        self.assertIsNotNone(event.published_at)
