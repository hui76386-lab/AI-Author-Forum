from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from ..crypto import EmailProtector
from ..models import EmailVerificationChallenge, InteractionOutbox
from ..rate_limits import RateLimitDecision
from ..services import request_email_verification
from ..tasks import cleanup_reader_security_records, send_magic_link


class AllowAllRateLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(True)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    READER_PUBLIC_BASE_URL="https://reader.example.org",
)
class ReaderEmailTaskTests(TestCase):
    databases = {"default", "interactions"}

    def create_event(self):
        result = request_email_verification(
            email="private-reader@example.org",
            purpose=EmailVerificationChallenge.Purpose.DOWNLOAD,
            return_path="/articles/example/",
            remote_address="192.0.2.20",
            user_agent="test",
            rate_limiter=AllowAllRateLimiter(),
            enqueue=lambda _event_id: None,
        )
        event = InteractionOutbox.objects.get(event_id=result.event_id)
        token = EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )
        return result, event, token

    def test_task_delivers_fragment_link_and_logs_no_pii(self):
        result, event, token = self.create_event()

        with self.assertLogs(
            "ai_author_forum.reader_interactions.tasks", level="INFO"
        ) as captured:
            task_result = send_magic_link.run(str(event.event_id))

        self.assertEqual(task_result, {"status": "sent"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"#token={token}", mail.outbox[0].body)
        self.assertIn("/reader-api/v1/verify-email/?challenge=", mail.outbox[0].body)
        self.assertNotIn("?token=", mail.outbox[0].body)
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)
        log_output = " ".join(captured.output)
        self.assertNotIn("private-reader@example.org", log_output)
        self.assertNotIn(token, log_output)
        self.assertIn(str(result.challenge_public_id), log_output)

    @patch(
        "ai_author_forum.reader_interactions.tasks.send_magic_link_email",
        side_effect=RuntimeError("private-reader@example.org token-secret"),
    )
    def test_provider_failure_is_retryable_and_redacted(self, _send):
        _result, event, _token = self.create_event()

        with (
            self.assertLogs(
                "ai_author_forum.reader_interactions.tasks", level="WARNING"
            ) as captured,
            self.assertRaisesRegex(RuntimeError, "Reader email delivery failed"),
        ):
            send_magic_link.run(str(event.event_id))

        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.last_error, "RuntimeError")
        self.assertIsNone(event.published_at)
        log_output = " ".join(captured.output)
        self.assertNotIn("private-reader@example.org", log_output)
        self.assertNotIn("token-secret", log_output)

    def test_cleanup_expires_and_removes_security_records_after_retention(self):
        _result, event, _token = self.create_event()
        challenge = EmailVerificationChallenge.objects.get(
            public_id=event.payload["challenge_id"]
        )
        EmailVerificationChallenge.objects.filter(pk=challenge.pk).update(
            expires_at=timezone.now() - timedelta(days=2)
        )

        result = cleanup_reader_security_records()

        self.assertEqual(result["expired_issued_challenges"], 1)
        self.assertEqual(result["deleted_challenges"], 1)
        self.assertFalse(
            EmailVerificationChallenge.objects.filter(pk=challenge.pk).exists()
        )
