import json
import logging
from io import StringIO
from pathlib import Path
from uuid import uuid4

import yaml
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ai_author_forum.settings.log_filters import JsonPrivacyFormatter

from ..observability import reader_metrics


class ObservabilityTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        with reader_metrics.lock:
            reader_metrics.requests.clear()
            reader_metrics.errors.clear()
            reader_metrics.durations.clear()
            reader_metrics.inflight = 0
            reader_metrics.scrape_errors = 0

    @override_settings(READER_INTERNAL_SERVICE_TOKEN="metrics-secret")
    def test_metrics_are_authenticated_low_cardinality_and_no_store(self):
        article_id = uuid4()
        self.client.get(
            reverse("reader_article_capabilities", args=[article_id]),
            HTTP_X_REQUEST_ID="capacity-request-1",
        )
        forbidden = self.client.get(reverse("reader_internal_metrics"))
        response = self.client.get(
            reverse("reader_internal_metrics"),
            HTTP_AUTHORIZATION="Bearer metrics-secret",
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.content.decode()
        self.assertIn("reader_http_requests_total", body)
        self.assertIn("reader_database_connection_limit", body)
        self.assertIn("reader_manifest_projection_match", body)
        self.assertIn(
            'route="/reader-api/v1/articles/<uuid:article_public_id>/capabilities/"',
            body,
        )
        self.assertNotIn(str(article_id), body)
        self.assertNotIn("capacity-request-1", body)

    def test_request_id_is_validated_and_propagated(self):
        accepted = self.client.get(
            reverse("reader_session"), HTTP_X_REQUEST_ID="synthetic:accepted"
        )
        rejected = self.client.get(
            reverse("reader_session"), HTTP_X_REQUEST_ID="bad request id"
        )

        self.assertEqual(accepted["X-Request-ID"], "synthetic:accepted")
        self.assertNotEqual(rejected["X-Request-ID"], "bad request id")
        self.assertEqual(rejected.json()["request_id"], rejected["X-Request-ID"])

    def test_privacy_formatter_emits_json_and_redacts_bearers(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonPrivacyFormatter())
        logger = logging.getLogger("reader-observability-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        logger.info(
            "email=reader@example.org Authorization: Bearer abcdefghijklmnop "
            "reader_session=session-secret "
            "/reader-api/v1/downloads/12345678-1234-1234-1234-123456789abc/"
            "abcdefghijklmnopqrstuvwxyz/?X-Amz-Signature=secret-signature",
            extra={"event": "privacy_test", "route": "/reader-api/test/"},
        )
        payload = json.loads(stream.getvalue())

        serialized = json.dumps(payload)
        self.assertEqual(payload["event"], "privacy_test")
        self.assertNotIn("reader@example.org", serialized)
        self.assertNotIn("abcdefghijklmnop", serialized)
        self.assertNotIn("session-secret", serialized)
        self.assertNotIn("secret-signature", serialized)

    def test_alert_rules_have_owner_and_runbook_without_pii_labels(self):
        path = (
            Path(__file__).resolve().parents[3]
            / "ops"
            / "prometheus"
            / "reader-interactions-alerts.yml"
        )
        groups = yaml.safe_load(path.read_text())["groups"]
        rules = [rule for group in groups for rule in group["rules"]]

        names = {rule["alert"] for rule in rules}
        self.assertTrue(
            {
                "ReaderStaticOrigin5xx",
                "ReaderApiErrorBudgetBurn",
                "ReaderDatabaseConnectionExhaustion",
                "ReaderDependencyUnavailable",
                "ReaderQueueBacklog",
                "ReaderEmailBounceOrComplaintSpike",
                "ReaderModerationBacklog",
                "ReaderPdfBuildFailures",
                "ReaderManifestProjectionMismatch",
                "ReaderObjectStorageAuthorizationFailures",
                "ReaderCostBudgetAnomaly",
            }.issubset(names)
        )
        for rule in rules:
            self.assertTrue(rule["labels"]["owner"])
            self.assertIn("runbook", rule["annotations"])
            serialized = json.dumps(rule).lower()
            for prohibited in ("email=", "reader_id", "article_id", "ip_address"):
                self.assertNotIn(prohibited, serialized)


@override_settings(READER_INTERNAL_SERVICE_TOKEN="metrics-secret")
def test_metrics_endpoint_rejects_query_token(client: Client):
    response = client.get(reverse("reader_internal_metrics") + "?token=metrics-secret")
    assert response.status_code == 403
