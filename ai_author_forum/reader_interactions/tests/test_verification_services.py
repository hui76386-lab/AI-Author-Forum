from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ..crypto import EmailProtector, email_lookup_digest, token_digest
from ..models import (
    EmailVerificationChallenge,
    InteractionOutbox,
    ReaderIdentity,
    ReaderSession,
)
from ..rate_limits import RateLimitDecision, RateLimitUnavailable
from ..services import (
    IdempotencyConflict,
    ReaderServiceError,
    VerificationInvalid,
    consume_email_verification,
    request_email_verification,
    resolve_session,
    revoke_session,
    update_reader_profile,
    validate_return_path,
)


class AllowAllRateLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(True)


class UnavailableRateLimiter:
    def check(self, dimensions, *, window_seconds):
        raise RateLimitUnavailable("offline")


@override_settings(
    READER_SESSION_ABSOLUTE_SECONDS=30 * 24 * 60 * 60,
    READER_SESSION_IDLE_SECONDS=14 * 24 * 60 * 60,
    READER_VERIFICATION_CONSUME_LIMIT=5,
)
class ReaderVerificationServiceTests(TestCase):
    databases = {"default", "interactions"}

    def request_challenge(self, email="Reader.Name+tag@Example.org", **overrides):
        queued = []
        values = {
            "email": email,
            "purpose": EmailVerificationChallenge.Purpose.COMMENT,
            "return_path": "/en/articles/example/",
            "remote_address": "192.0.2.10",
            "user_agent": "test-browser",
            "rate_limiter": AllowAllRateLimiter(),
            "enqueue": queued.append,
        }
        values.update(overrides)
        result = request_email_verification(**values)
        return result, queued

    def token_for(self, result):
        event = InteractionOutbox.objects.get(event_id=result.event_id)
        return EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )

    def test_request_stores_only_protected_values_and_supersedes_same_purpose(self):
        first, first_queue = self.request_challenge()
        token = self.token_for(first)
        first_challenge = EmailVerificationChallenge.objects.get(
            public_id=first.challenge_public_id
        )
        event = InteractionOutbox.objects.get(event_id=first.event_id)

        self.assertEqual(first_queue, [first.event_id])
        self.assertNotIn("Reader.Name", first_challenge.email_ciphertext)
        self.assertNotEqual(first_challenge.token_hash, token)
        self.assertNotIn(token, str(event.payload))
        self.assertNotIn("Reader.Name", str(event.payload))

        second, _queue = self.request_challenge()
        first_challenge.refresh_from_db()
        self.assertEqual(
            first_challenge.status,
            EmailVerificationChallenge.Status.SUPERSEDED,
        )
        self.assertNotEqual(first.challenge_public_id, second.challenge_public_id)

    def test_invalid_and_open_redirect_requests_are_neutral_and_not_persisted(self):
        invalid, _queue = self.request_challenge(email="not-an-email")
        redirect, _queue = self.request_challenge(return_path="https://evil.example/")

        self.assertTrue(invalid.accepted)
        self.assertTrue(redirect.accepted)
        self.assertIsNone(invalid.challenge_public_id)
        self.assertIsNone(redirect.challenge_public_id)
        self.assertEqual(EmailVerificationChallenge.objects.count(), 0)
        for path in ("//evil.example/path", "/\\evil", "/admin/"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                validate_return_path(path)

    def test_request_fails_closed_when_redis_is_unavailable(self):
        with self.assertRaises(ReaderServiceError):
            self.request_challenge(rate_limiter=UnavailableRateLimiter())
        self.assertEqual(EmailVerificationChallenge.objects.count(), 0)

    def test_queue_failure_leaves_reconcilable_outbox_without_pii_log(self):
        def fail_enqueue(_event_id):
            raise RuntimeError("Reader.Name+tag@example.org token-secret")

        with self.assertLogs(
            "ai_author_forum.reader_interactions.services", level="WARNING"
        ) as captured:
            result, _queue = self.request_challenge(enqueue=fail_enqueue)

        self.assertFalse(result.enqueued)
        event = InteractionOutbox.objects.get(event_id=result.event_id)
        self.assertIsNone(event.published_at)
        log_output = " ".join(captured.output)
        self.assertNotIn("Reader.Name", log_output)
        self.assertNotIn("token-secret", log_output)

    def test_consume_is_single_use_and_creates_hashed_session(self):
        result, _queue = self.request_challenge()
        token = self.token_for(result)

        consumed = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            display_name="Public Reader",
            remote_address="192.0.2.10",
            user_agent="test-browser",
            rate_limiter=AllowAllRateLimiter(),
        )

        challenge = EmailVerificationChallenge.objects.get(
            public_id=result.challenge_public_id
        )
        self.assertEqual(challenge.status, EmailVerificationChallenge.Status.CONSUMED)
        self.assertEqual(ReaderIdentity.objects.count(), 1)
        self.assertNotEqual(consumed.session.secret_hash, consumed.session_secret)
        self.assertEqual(
            consumed.session.secret_hash, token_digest(consumed.session_secret)
        )
        self.assertNotIn("@", consumed.reader.email_ciphertext)
        replay = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            display_name="Public Reader",
            remote_address="192.0.2.10",
            user_agent="test-browser",
            rate_limiter=AllowAllRateLimiter(),
        )
        self.assertEqual(replay.session_secret, consumed.session_secret)
        self.assertEqual(ReaderSession.objects.count(), 1)

    def test_expired_and_repeated_invalid_tokens_reach_terminal_states(self):
        expired, _queue = self.request_challenge(email="expired@example.org")
        EmailVerificationChallenge.objects.filter(
            public_id=expired.challenge_public_id
        ).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(VerificationInvalid):
            consume_email_verification(
                challenge_public_id=expired.challenge_public_id,
                token=self.token_for(expired),
                display_name="Reader",
                remote_address="192.0.2.10",
                user_agent="test",
                rate_limiter=AllowAllRateLimiter(),
            )
        self.assertEqual(
            EmailVerificationChallenge.objects.get(
                public_id=expired.challenge_public_id
            ).status,
            EmailVerificationChallenge.Status.EXPIRED,
        )

        blocked, _queue = self.request_challenge(email="blocked@example.org")
        for _index in range(5):
            with self.assertRaises(VerificationInvalid):
                consume_email_verification(
                    challenge_public_id=blocked.challenge_public_id,
                    token="wrong-token",
                    display_name="Reader",
                    remote_address="192.0.2.11",
                    user_agent="test",
                    rate_limiter=AllowAllRateLimiter(),
                )
        challenge = EmailVerificationChallenge.objects.get(
            public_id=blocked.challenge_public_id
        )
        self.assertEqual(challenge.attempts, 5)
        self.assertEqual(challenge.status, EmailVerificationChallenge.Status.BLOCKED)

    def test_session_expiry_revoke_and_profile_idempotency(self):
        result, _queue = self.request_challenge(email="profile@example.org")
        consumed = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=self.token_for(result),
            display_name="Initial Name",
            remote_address="192.0.2.12",
            user_agent="test",
            rate_limiter=AllowAllRateLimiter(),
        )
        self.assertIsNotNone(resolve_session(consumed.session_secret))

        response = update_reader_profile(
            reader=consumed.reader,
            display_name="Updated Name",
            expected_version=1,
            idempotency_key="profile-operation-1",
            request_hash="a" * 64,
        )
        replay = update_reader_profile(
            reader=consumed.reader,
            display_name="Updated Name",
            expected_version=1,
            idempotency_key="profile-operation-1",
            request_hash="a" * 64,
        )
        self.assertEqual(response, replay)
        with self.assertRaises(IdempotencyConflict):
            update_reader_profile(
                reader=consumed.reader,
                display_name="Different Name",
                expected_version=2,
                idempotency_key="profile-operation-1",
                request_hash="b" * 64,
            )

        self.assertEqual(revoke_session(consumed.session_secret), 1)
        self.assertEqual(revoke_session(consumed.session_secret), 0)
        self.assertIsNone(resolve_session(consumed.session_secret))

    def test_session_rotates_and_enforces_idle_and_absolute_expiry(self):
        first, _queue = self.request_challenge(email="rotation@example.org")
        consumed_first = consume_email_verification(
            challenge_public_id=first.challenge_public_id,
            token=self.token_for(first),
            display_name="Reader",
            remote_address="192.0.2.13",
            user_agent="test",
            rate_limiter=AllowAllRateLimiter(),
        )
        second, _queue = self.request_challenge(email="rotation@example.org")
        consumed_second = consume_email_verification(
            challenge_public_id=second.challenge_public_id,
            token=self.token_for(second),
            display_name="Reader",
            remote_address="192.0.2.13",
            user_agent="test",
            existing_session_secret=consumed_first.session_secret,
            rate_limiter=AllowAllRateLimiter(),
        )
        consumed_first.session.refresh_from_db()
        self.assertIsNotNone(consumed_first.session.revoked_at)
        self.assertIsNone(resolve_session(consumed_first.session_secret))
        self.assertIsNotNone(resolve_session(consumed_second.session_secret))

        ReaderSession.objects.filter(pk=consumed_second.session.pk).update(
            idle_expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIsNone(resolve_session(consumed_second.session_secret))
        consumed_second.session.refresh_from_db()
        self.assertIsNotNone(consumed_second.session.revoked_at)

        third, _queue = self.request_challenge(email="absolute@example.org")
        consumed_third = consume_email_verification(
            challenge_public_id=third.challenge_public_id,
            token=self.token_for(third),
            display_name="Reader",
            remote_address="192.0.2.14",
            user_agent="test",
            rate_limiter=AllowAllRateLimiter(),
        )
        ReaderSession.objects.filter(pk=consumed_third.session.pk).update(
            absolute_expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIsNone(resolve_session(consumed_third.session_secret))

    def test_email_hmac_matches_normalized_address(self):
        result, _queue = self.request_challenge(email="Reader@BÜCHER.example")
        challenge = EmailVerificationChallenge.objects.get(
            public_id=result.challenge_public_id
        )
        self.assertEqual(
            challenge.email_lookup_hmac,
            email_lookup_digest("Reader@xn--bcher-kva.example"),
        )


@skipUnless(
    connections["interactions"].vendor == "postgresql",
    "Verification lock acceptance requires PostgreSQL.",
)
@override_settings(
    READER_SESSION_ABSOLUTE_SECONDS=30 * 24 * 60 * 60,
    READER_SESSION_IDLE_SECONDS=14 * 24 * 60 * 60,
    READER_VERIFICATION_CONSUME_LIMIT=5,
)
class ReaderVerificationConcurrencyTests(TransactionTestCase):
    databases = {"default", "interactions"}

    def _consume(self, barrier, challenge_id, token):
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            consume_email_verification(
                challenge_public_id=challenge_id,
                token=token,
                display_name="Concurrent Reader",
                remote_address="192.0.2.30",
                user_agent="test",
                rate_limiter=AllowAllRateLimiter(),
            )
        except VerificationInvalid:
            return "invalid"
        finally:
            connections.close_all()
        return "consumed"

    def test_concurrent_token_consumption_creates_exactly_one_session(self):
        result = request_email_verification(
            email="concurrent@example.org",
            purpose=EmailVerificationChallenge.Purpose.SESSION,
            return_path="/",
            remote_address="192.0.2.30",
            user_agent="test",
            rate_limiter=AllowAllRateLimiter(),
            enqueue=lambda _event_id: None,
        )
        event = InteractionOutbox.objects.get(event_id=result.event_id)
        token = EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._consume,
                    barrier,
                    result.challenge_public_id,
                    token,
                )
                for _index in range(2)
            ]
        self.assertCountEqual(
            [future.result() for future in futures], ["consumed", "invalid"]
        )
        self.assertEqual(ReaderSession.objects.count(), 1)

    def _request(self, barrier):
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            return request_email_verification(
                email="concurrent-request@example.org",
                purpose=EmailVerificationChallenge.Purpose.COMMENT,
                return_path="/",
                remote_address="192.0.2.31",
                user_agent="test",
                rate_limiter=AllowAllRateLimiter(),
                enqueue=lambda _event_id: None,
            )
        finally:
            connections.close_all()

    def test_concurrent_requests_leave_one_issued_challenge(self):
        barrier = Barrier(3)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(self._request, barrier) for _index in range(3)]
        self.assertTrue(all(future.result().accepted for future in futures))
        challenges = EmailVerificationChallenge.objects.filter(
            email_lookup_hmac=email_lookup_digest("concurrent-request@example.org"),
            purpose=EmailVerificationChallenge.Purpose.COMMENT,
        )
        self.assertEqual(
            challenges.filter(status=EmailVerificationChallenge.Status.ISSUED).count(),
            1,
        )
        self.assertEqual(
            challenges.filter(
                status=EmailVerificationChallenge.Status.SUPERSEDED
            ).count(),
            2,
        )
