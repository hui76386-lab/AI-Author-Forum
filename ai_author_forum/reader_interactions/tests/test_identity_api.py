import json
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..crypto import EmailProtector
from ..models import (
    ArticleCapabilityProjection,
    EmailVerificationChallenge,
    InteractionOutbox,
    ReaderSession,
)
from ..rate_limits import RateLimitDecision


class AllowAllRateLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(True)


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_EMAIL_VERIFICATION_ENABLED=True,
    READER_COMMENTS_WRITE_ENABLED=True,
    READER_SESSION_COOKIE_SECURE=True,
)
class ReaderIdentityApiTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.get(reverse("reader_session"), secure=True)
        self.csrf_token = self.client.cookies["csrftoken"].value
        self.rate_patch = patch(
            "ai_author_forum.reader_interactions.services.RedisAtomicRateLimiter",
            return_value=AllowAllRateLimiter(),
        )
        self.queue_patch = patch(
            "ai_author_forum.reader_interactions.services._enqueue_magic_link"
        )
        self.rate_patch.start()
        self.queue_patch.start()
        self.addCleanup(self.rate_patch.stop)
        self.addCleanup(self.queue_patch.stop)

    def post_json(self, url, body, **headers):
        headers.setdefault("HTTP_ORIGIN", "https://testserver")
        return self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf_token,
            secure=True,
            **headers,
        )

    def request_challenge(
        self, email="reader@example.org", return_to="/articles/example/"
    ):
        response = self.post_json(
            reverse("reader_email_verification_request"),
            {"email": email, "return_to": return_to, "intent": "comment"},
            HTTP_X_REQUEST_ID="test-request-id",
        )
        challenge = EmailVerificationChallenge.objects.order_by("-pk").first()
        event = InteractionOutbox.objects.get(
            aggregate_id=str(challenge.public_id),
            event_type="reader.email.magic_link.requested",
        )
        token = EmailProtector.from_settings().decrypt_text(
            event.payload["delivery_token_ciphertext"]
        )
        return response, challenge, token

    def test_anonymous_session_returns_200_and_sets_csrf_without_email(self):
        response = self.client.get(reverse("reader_session"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["authenticated"])
        self.assertIn("csrftoken", response.cookies)
        self.assertNotIn("email", response.content.decode().lower())
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_request_response_is_neutral_and_open_redirect_is_not_persisted(self):
        valid, _challenge, _token = self.request_challenge()
        count = EmailVerificationChallenge.objects.count()
        invalid = self.post_json(
            reverse("reader_email_verification_request"),
            {
                "email": "reader@example.org",
                "return_to": "https://evil.example/steal",
                "intent": "comment",
            },
            HTTP_X_REQUEST_ID="test-request-id",
        )

        self.assertEqual(valid.status_code, 202)
        self.assertEqual(invalid.status_code, 202)
        self.assertTrue(valid.json()["data"]["flow_id"])
        self.assertNotIn("user_code", valid.json()["data"])
        self.assertEqual(invalid.json()["data"], {"accepted": True})
        self.assertEqual(EmailVerificationChallenge.objects.count(), count)

        invalid_email = self.post_json(
            reverse("reader_email_verification_request"),
            {"email": "not-an-email", "return_to": "/", "intent": "session"},
            HTTP_X_REQUEST_ID="test-request-id",
        )
        self.assertEqual(invalid_email.status_code, 202)
        self.assertEqual(invalid_email.json()["data"], {"accepted": True})
        self.assertEqual(EmailVerificationChallenge.objects.count(), count)

    def test_verification_writes_require_csrf(self):
        response = Client(enforce_csrf_checks=True).post(
            reverse("reader_email_verification_request"),
            data=json.dumps({"email": "reader@example.org"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_verification_writes_reject_untrusted_origin(self):
        response = self.client.post(
            reverse("reader_email_verification_request"),
            data=json.dumps({"email": "reader@example.org"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf_token,
            HTTP_ORIGIN="https://evil.example",
            secure=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_get_confirmation_does_not_consume_and_hides_sensitive_values(self):
        _response, challenge, token = self.request_challenge()
        response = self.client.get(
            reverse("reader_email_verification_confirm", args=[challenge.public_id]),
            secure=True,
        )

        challenge.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(challenge.status, EmailVerificationChallenge.Status.ISSUED)
        body = response.content.decode()
        self.assertNotIn(token, body)
        self.assertNotIn("reader@example.org", body)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])

        canonical_response = self.client.get(
            reverse("reader_verify_email"),
            {"challenge": str(challenge.public_id)},
            secure=True,
        )
        self.assertEqual(canonical_response.status_code, 200)
        self.assertIn(
            reverse("reader_email_verification_consume", args=[challenge.public_id]),
            canonical_response.content.decode(),
        )

    def test_consume_session_profile_replay_and_logout_end_to_end(self):
        _response, challenge, token = self.request_challenge()
        consume_url = reverse(
            "reader_email_verification_consume", args=[challenge.public_id]
        )
        consumed = self.post_json(
            consume_url,
            {"token": token, "display_name": "Public Reader"},
        )

        self.assertEqual(consumed.status_code, 200)
        cookie = consumed.cookies["reader_session"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/reader-api/")
        self.assertNotIn(token, consumed.content.decode())
        self.assertNotIn("reader@example.org", consumed.content.decode())

        replay = self.post_json(
            consume_url,
            {"token": token, "display_name": "Public Reader"},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(ReaderSession.objects.count(), 1)

        session = self.client.get(reverse("reader_session"), secure=True)
        self.assertTrue(session.json()["data"]["authenticated"])
        self.assertNotIn("email", session.content.decode().lower())

        article_ids = (uuid4(), uuid4())
        for index, article_id in enumerate(article_ids, start=1):
            ArticleCapabilityProjection.objects.create(
                article_public_id=article_id,
                journal_id=index,
                active_release="global-reader-session-release",
                approved_revision_id=index,
                comments_mode=ArticleCapabilityProjection.CommentsMode.OPEN,
                download_enabled=False,
                policy_version=1,
                projection_version=1,
                applied_at=timezone.now(),
            )
        with patch(
            "ai_author_forum.reader_interactions.capabilities.CapabilityDenyStore.get_desired",
            return_value=None,
        ):
            capabilities = [
                self.client.get(
                    reverse("reader_article_capabilities", args=[article_id]),
                    secure=True,
                ).json()["data"]
                for article_id in article_ids
            ]
        self.assertTrue(all(item["can_comment"] for item in capabilities))
        self.assertTrue(all(not item["verification_required"] for item in capabilities))

        version = session.json()["data"]["reader"]["version"]
        profile_body = {"display_name": "Updated Reader", "expected_version": version}
        profile = self.client.patch(
            reverse("reader_session_profile"),
            data=json.dumps(profile_body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf_token,
            HTTP_IDEMPOTENCY_KEY="profile-operation-1",
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        profile_replay = self.client.patch(
            reverse("reader_session_profile"),
            data=json.dumps(profile_body),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf_token,
            HTTP_IDEMPOTENCY_KEY="profile-operation-1",
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["data"], profile_replay.json()["data"])

        logout = self.client.post(
            reverse("reader_session_logout"),
            HTTP_X_CSRFTOKEN=self.csrf_token,
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.cookies["reader_session"]["max-age"], 0)
        self.assertTrue(logout.cookies["reader_session"]["secure"])
        self.assertTrue(logout.cookies["reader_session"]["httponly"])
        self.assertFalse(
            self.client.get(reverse("reader_session"), secure=True).json()["data"][
                "authenticated"
            ]
        )

    @override_settings(
        READER_INTERACTIONS_ENABLED=False,
        READER_EMAIL_VERIFICATION_ENABLED=False,
    )
    def test_disabled_flags_fail_closed_without_affecting_session_probe(self):
        session = self.client.get(reverse("reader_session"), secure=True)
        request = self.post_json(
            reverse("reader_email_verification_request"),
            {"email": "reader@example.org"},
        )
        self.assertEqual(session.status_code, 200)
        self.assertFalse(session.json()["data"]["authenticated"])
        self.assertEqual(request.status_code, 503)
