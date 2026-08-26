import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..crypto import token_digest
from ..models import (
    ArticleCapabilityProjection,
    IdempotencyRecord,
    InteractionOutbox,
    ReaderActionEvent,
    ReaderIdentity,
    ReaderSession,
)


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_SHARE_UI_ENABLED=True,
    READER_SESSION_COOKIE_SECURE=True,
)
class ShareEventApiTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.article_id = uuid4()
        self.reader = ReaderIdentity.objects.create(
            email_ciphertext="ciphertext",
            email_lookup_hmac=uuid4().hex + uuid4().hex,
            email_key_version=1,
            email_verified_at=timezone.now(),
            display_name="Share Reader",
        )
        self.secret = "share-reader-session"
        now = timezone.now()
        ReaderSession.objects.create(
            reader=self.reader,
            secret_hash=token_digest(self.secret),
            last_seen_at=now,
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=2),
        )
        ArticleCapabilityProjection.objects.create(
            article_public_id=self.article_id,
            journal_id=1,
            active_release="share-release-1",
            approved_revision_id=1,
            comments_mode=ArticleCapabilityProjection.CommentsMode.READ_ONLY,
            download_enabled=False,
            policy_version=1,
            projection_version=1,
            applied_at=now,
        )
        self.deny_patch = patch(
            "ai_author_forum.reader_interactions.capabilities.CapabilityDenyStore.get_desired",
            return_value=None,
        )
        self.deny_patch.start()
        self.addCleanup(self.deny_patch.stop)
        self.client = Client(enforce_csrf_checks=True)
        self.client.cookies["reader_session"] = self.secret
        self.client.get(reverse("reader_session"), secure=True)
        self.csrf = self.client.cookies["csrftoken"].value

    def post(self, body, *, key="share-operation-1", client=None):
        return (client or self.client).post(
            reverse("reader_share_event", args=[self.article_id]),
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
            HTTP_IDEMPOTENCY_KEY=key,
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )

    def test_capability_uses_the_same_reader_session_for_share(self):
        authenticated = self.client.get(
            reverse("reader_article_capabilities", args=[self.article_id]), secure=True
        )
        anonymous = Client().get(
            reverse("reader_article_capabilities", args=[self.article_id]), secure=True
        )

        self.assertTrue(authenticated.json()["data"]["share_available"])
        self.assertTrue(authenticated.json()["data"]["can_share"])
        self.assertTrue(anonymous.json()["data"]["share_available"])
        self.assertFalse(anonymous.json()["data"]["can_share"])
        self.assertTrue(anonymous.json()["data"]["verification_required"])

    def test_records_minimal_event_outbox_and_idempotent_replay(self):
        response = self.post({"action": "system_share", "outcome": "completed"})
        replay = self.post({"action": "system_share", "outcome": "completed"})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(response.json()["data"], replay.json()["data"])
        event = ReaderActionEvent.objects.get()
        outbox = InteractionOutbox.objects.get()
        self.assertEqual(event.event_type, ReaderActionEvent.EventType.SHARE_OPENED)
        self.assertEqual(event.outcome, "completed")
        self.assertEqual(event.reader_public_id, self.reader.public_id)
        self.assertEqual(outbox.payload["release_version"], "share-release-1")
        serialized = json.dumps(outbox.payload).lower()
        self.assertNotIn("recipient", serialized)
        self.assertNotIn("message", serialized)
        self.assertNotIn("email", serialized)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_coalesces_same_action_for_a_minute_but_keeps_action_types_separate(self):
        first = self.post(
            {"action": "system_share", "outcome": "cancelled"}, key="share-first"
        )
        second = self.post(
            {"action": "system_share", "outcome": "failed"}, key="share-second"
        )
        copied = self.post(
            {"action": "copy_link", "outcome": "failed"}, key="copy-first"
        )

        self.assertTrue(first.json()["data"]["recorded"])
        self.assertTrue(second.json()["data"]["coalesced"])
        self.assertTrue(copied.json()["data"]["recorded"])
        self.assertEqual(ReaderActionEvent.objects.count(), 2)

    def test_rejects_extra_recipient_invalid_choice_conflict_and_missing_csrf(self):
        extra = self.post(
            {
                "action": "system_share",
                "outcome": "completed",
                "recipient": "not-accepted@example.org",
            },
            key="extra",
        )
        invalid = self.post(
            {"action": "send_message", "outcome": "completed"}, key="invalid"
        )
        accepted = self.post(
            {"action": "copy_link", "outcome": "completed"}, key="conflict"
        )
        conflict = self.post(
            {"action": "copy_link", "outcome": "failed"}, key="conflict"
        )
        no_csrf = Client(enforce_csrf_checks=True).post(
            reverse("reader_share_event", args=[self.article_id]),
            data=json.dumps({"action": "copy_link", "outcome": "completed"}),
            content_type="application/json",
            secure=True,
        )

        self.assertEqual(extra.status_code, 422)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(no_csrf.status_code, 403)

    @override_settings(READER_SHARE_UI_ENABLED=False)
    def test_disabled_flag_fails_closed(self):
        response = self.post(
            {"action": "copy_link", "outcome": "completed"}, key="disabled"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "share_disabled")
        self.assertFalse(ReaderActionEvent.objects.exists())
