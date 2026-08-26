import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import (
    Client,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone

from ..crypto import EmailProtector
from ..models import (
    EmailVerificationChallenge,
    InteractionOutbox,
    ReaderDeviceFlow,
    ReaderSession,
)
from ..rate_limits import RateLimitDecision
from ..services import (
    VerificationInvalid,
    claim_device_flow,
    consume_email_verification,
    get_device_flow_status,
    request_email_verification,
)


class AllowAllRateLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(True)


@override_settings(
    READER_DEVICE_FLOW_ATTEMPT_LIMIT=5,
    READER_DEVICE_FLOW_TTL_SECONDS=900,
    READER_SESSION_COOKIE_SECURE=True,
)
class ReaderDeviceFlowTests(TestCase):
    databases = {"default", "interactions"}

    def issue(self):
        result = request_email_verification(
            email="cross-device@example.org",
            purpose=EmailVerificationChallenge.Purpose.COMMENT,
            return_path="/articles/example/",
            remote_address="192.0.2.50",
            user_agent="desktop",
            rate_limiter=AllowAllRateLimiter(),
            enqueue=lambda _event_id: None,
        )
        event = InteractionOutbox.objects.get(event_id=result.event_id)
        token = EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )
        return result, token

    def test_pairing_approves_then_claims_only_on_origin_device(self):
        result, token = self.issue()
        flow = ReaderDeviceFlow.objects.get(public_id=result.flow_public_id)
        self.assertNotEqual(flow.user_code_hash, result.user_code)
        with self.assertRaises(VerificationInvalid):
            consume_email_verification(
                challenge_public_id=result.challenge_public_id,
                token=token,
                user_code="AAAA-BBBB",
                display_name="Reader",
                remote_address="192.0.2.51",
                user_agent="phone",
                rate_limiter=AllowAllRateLimiter(),
            )
        flow.refresh_from_db()
        self.assertEqual(flow.attempts, 1)
        consumed = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            user_code=result.user_code,
            display_name="Reader",
            remote_address="192.0.2.51",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )
        flow.refresh_from_db()
        self.assertTrue(consumed.paired)
        self.assertEqual(flow.status, ReaderDeviceFlow.Status.APPROVED)
        with self.assertRaises(VerificationInvalid):
            get_device_flow_status(
                flow_public_id=result.flow_public_id,
                origin_cookie_secret="wrong-device",
            )
        status = get_device_flow_status(
            flow_public_id=result.flow_public_id,
            origin_cookie_secret=result.origin_cookie_secret,
        )
        self.assertEqual(status["status"], ReaderDeviceFlow.Status.APPROVED)
        claim = claim_device_flow(
            flow_public_id=result.flow_public_id,
            origin_cookie_secret=result.origin_cookie_secret,
            remote_address="192.0.2.50",
            user_agent="desktop",
        )
        self.assertTrue(claim.session_secret)
        self.assertEqual(ReaderSession.objects.count(), 2)

        replay = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            user_code=result.user_code,
            remote_address="192.0.2.51",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )
        self.assertEqual(replay.session_secret, consumed.session_secret)
        self.assertEqual(ReaderSession.objects.count(), 2)
        replay = claim_device_flow(
            flow_public_id=result.flow_public_id,
            origin_cookie_secret=result.origin_cookie_secret,
            remote_address="192.0.2.50",
            user_agent="desktop",
        )
        self.assertTrue(replay.already_claimed)
        self.assertEqual(replay.session_secret, claim.session_secret)
        self.assertEqual(ReaderSession.objects.count(), 2)

    def test_email_link_without_code_approves_origin_flow(self):
        result, token = self.issue()
        consumed = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            remote_address="192.0.2.51",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )
        self.assertTrue(consumed.paired)
        self.assertEqual(
            ReaderDeviceFlow.objects.get(public_id=result.flow_public_id).status,
            ReaderDeviceFlow.Status.APPROVED,
        )

    def test_email_link_replay_without_code_is_idempotent(self):
        result, token = self.issue()
        first = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            remote_address="192.0.2.51",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )
        replay = consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            remote_address="192.0.2.51",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )
        self.assertTrue(replay.paired)
        self.assertEqual(replay.session_secret, first.session_secret)
        self.assertEqual(
            EmailVerificationChallenge.objects.get(
                public_id=result.challenge_public_id
            ).status,
            EmailVerificationChallenge.Status.CONSUMED,
        )

    def test_five_wrong_codes_deny_the_flow(self):
        result, token = self.issue()
        for _index in range(5):
            with self.assertRaises(VerificationInvalid):
                consume_email_verification(
                    challenge_public_id=result.challenge_public_id,
                    token=token,
                    user_code="AAAA-BBBB",
                    display_name="Reader",
                    remote_address="192.0.2.51",
                    user_agent="phone",
                    rate_limiter=AllowAllRateLimiter(),
                )
        self.assertEqual(
            ReaderDeviceFlow.objects.get(public_id=result.flow_public_id).status,
            ReaderDeviceFlow.Status.DENIED,
        )

    def test_expired_flow_is_terminal(self):
        result, _token = self.issue()
        ReaderDeviceFlow.objects.filter(public_id=result.flow_public_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        status = get_device_flow_status(
            flow_public_id=result.flow_public_id,
            origin_cookie_secret=result.origin_cookie_secret,
        )
        self.assertEqual(status["status"], ReaderDeviceFlow.Status.EXPIRED)

    def test_expired_approved_flow_is_terminal_for_status_and_claim(self):
        result, token = self.issue()
        consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            user_code=result.user_code,
            remote_address="192.0.2.51",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )
        ReaderDeviceFlow.objects.filter(public_id=result.flow_public_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        status = get_device_flow_status(
            flow_public_id=result.flow_public_id,
            origin_cookie_secret=result.origin_cookie_secret,
        )
        self.assertEqual(status["status"], ReaderDeviceFlow.Status.EXPIRED)
        with self.assertRaises(VerificationInvalid):
            claim_device_flow(
                flow_public_id=result.flow_public_id,
                origin_cookie_secret=result.origin_cookie_secret,
                remote_address="192.0.2.50",
                user_agent="desktop",
            )


@skipUnless(
    connections["interactions"].vendor == "postgresql",
    "Device-flow row-lock concurrency requires PostgreSQL",
)
class ReaderDeviceFlowConcurrencyTests(TransactionTestCase):
    databases = {"default", "interactions"}

    def issue(self):
        result = request_email_verification(
            email="concurrent-cross-device@example.org",
            purpose=EmailVerificationChallenge.Purpose.COMMENT,
            return_path="/articles/example/",
            remote_address="192.0.2.60",
            user_agent="desktop",
            rate_limiter=AllowAllRateLimiter(),
            enqueue=lambda _event_id: None,
        )
        event = InteractionOutbox.objects.get(event_id=result.event_id)
        token = EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )
        return result, token

    @staticmethod
    def run_in_isolated_connection(callback):
        close_old_connections()
        try:
            return callback()
        finally:
            close_old_connections()

    def test_two_phones_confirm_one_flow_without_duplicate_sessions(self):
        result, token = self.issue()

        def consume():
            return self.run_in_isolated_connection(
                lambda: consume_email_verification(
                    challenge_public_id=result.challenge_public_id,
                    token=token,
                    remote_address="192.0.2.61",
                    user_agent="phone",
                    rate_limiter=AllowAllRateLimiter(),
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _item: consume(), range(2)))

        self.assertEqual(
            {item.session_secret for item in outcomes}, {outcomes[0].session_secret}
        )
        self.assertTrue(all(item.paired for item in outcomes))
        self.assertEqual(ReaderSession.objects.count(), 1)
        self.assertEqual(
            ReaderDeviceFlow.objects.get(public_id=result.flow_public_id).status,
            ReaderDeviceFlow.Status.APPROVED,
        )

    def test_two_computers_claim_one_flow_without_duplicate_sessions(self):
        result, token = self.issue()
        consume_email_verification(
            challenge_public_id=result.challenge_public_id,
            token=token,
            remote_address="192.0.2.61",
            user_agent="phone",
            rate_limiter=AllowAllRateLimiter(),
        )

        def claim():
            return self.run_in_isolated_connection(
                lambda: claim_device_flow(
                    flow_public_id=result.flow_public_id,
                    origin_cookie_secret=result.origin_cookie_secret,
                    remote_address="192.0.2.60",
                    user_agent="desktop",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _item: claim(), range(2)))

        self.assertEqual(
            {item.session_secret for item in outcomes},
            {outcomes[0].session_secret},
        )
        self.assertEqual(sum(item.already_claimed for item in outcomes), 1)
        self.assertEqual(ReaderSession.objects.count(), 2)
        self.assertEqual(
            ReaderDeviceFlow.objects.get(public_id=result.flow_public_id).status,
            ReaderDeviceFlow.Status.CLAIMED,
        )


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_EMAIL_VERIFICATION_ENABLED=True,
    READER_SESSION_COOKIE_SECURE=True,
    READER_DEVICE_FLOW_COOKIE_SECURE=True,
)
class ReaderDeviceFlowApiTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.desktop = Client(enforce_csrf_checks=True)
        self.phone = Client(enforce_csrf_checks=True)
        for client in (self.desktop, self.phone):
            client.get(reverse("reader_session"), secure=True)
        self.desktop_csrf = self.desktop.cookies["csrftoken"].value
        self.phone_csrf = self.phone.cookies["csrftoken"].value
        self.rate_patch = patch(
            "ai_author_forum.reader_interactions.services.RedisAtomicRateLimiter",
            return_value=AllowAllRateLimiter(),
        )
        self.api_rate_patch = patch(
            "ai_author_forum.reader_interactions.api.RedisAtomicRateLimiter",
            return_value=AllowAllRateLimiter(),
        )
        self.enqueue_patch = patch(
            "ai_author_forum.reader_interactions.services._enqueue_magic_link"
        )
        self.rate_patch.start()
        self.api_rate_patch.start()
        self.enqueue_patch.start()
        self.addCleanup(self.rate_patch.stop)
        self.addCleanup(self.api_rate_patch.stop)
        self.addCleanup(self.enqueue_patch.stop)

    def post(self, client, csrf, url, body):
        return client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )

    def test_desktop_and_phone_receive_independent_sessions(self):
        requested = self.post(
            self.desktop,
            self.desktop_csrf,
            reverse("reader_email_verification_request"),
            {
                "email": "api-cross-device@example.org",
                "intent": "comment",
                "return_to": "/articles/example/",
            },
        )
        self.assertEqual(requested.status_code, 202)
        payload = requested.json()["data"]
        flow = ReaderDeviceFlow.objects.get(public_id=payload["flow_id"])
        challenge = flow.challenge
        event = InteractionOutbox.objects.get(
            aggregate_id=str(challenge.public_id),
            event_type="reader.email.magic_link.requested",
        )
        token = EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )
        consumed = self.post(
            self.phone,
            self.phone_csrf,
            reverse("reader_email_verification_consume", args=[challenge.public_id]),
            {"token": token},
        )
        self.assertEqual(consumed.status_code, 200)
        self.assertTrue(consumed.json()["data"]["paired"])
        status = self.desktop.get(
            reverse("reader_device_flow_status", args=[payload["flow_id"]]),
            secure=True,
            HTTP_ORIGIN="https://testserver",
        )
        self.assertEqual(status.json()["data"]["status"], "approved")
        claimed = self.post(
            self.desktop,
            self.desktop_csrf,
            reverse("reader_device_flow_claim", args=[payload["flow_id"]]),
            {},
        )
        self.assertEqual(claimed.status_code, 200)
        self.assertNotEqual(
            consumed.cookies["reader_session"].value,
            claimed.cookies["reader_session"].value,
        )
