import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from ..health import broker_health
from ..models import StaticManifest, StaticPublishJob


class StaticPublishHealthTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings_override = override_settings(
            STATIC_PUBLISH_ROOT=self.temporary.name,
            STATIC_PUBLISH_HEALTHCHECK_BROKER=False,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def create_active_release(self, version="release-ready"):
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            scope=StaticPublishJob.Scope.FULL,
            version=version,
        )
        StaticManifest.objects.create(
            version=version,
            job=job,
            files=[],
            metadata={"summary": {"failed": 0}},
            is_active=True,
        )
        current = Path(self.temporary.name, "current")
        current.mkdir(parents=True)
        (current / "manifest.json").write_text(
            json.dumps({"version": version, "summary": {"failed": 0}}),
            encoding="utf-8",
        )

    @patch("ai_author_forum.static_publish.health_views.get_health_report")
    def test_livez_does_not_check_dependencies(self, get_health_report):
        response = self.client.get(reverse("livez"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"process": {"ok": True}})
        get_health_report.assert_not_called()

    def test_healthz_checks_database_without_requiring_a_release(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertNotIn("release", response.json()["checks"])

    def test_readiness_fails_when_no_active_release_exists(self):
        response = self.client.get(reverse("readyz"))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["release"]["ok"])

    def test_readiness_passes_for_matching_disk_and_database_manifest(self):
        self.create_active_release()

        response = self.client.get(reverse("readyz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @override_settings(STATIC_PUBLISH_HEALTHCHECK_BROKER=True)
    @patch("ai_author_forum.static_publish.health.broker_health")
    def test_readiness_includes_broker_when_enabled(self, broker_health):
        self.create_active_release()
        broker_health.return_value = (False, "task broker unavailable: OSError")

        response = self.client.get(reverse("readyz"))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["broker"]["ok"])

    @override_settings(
        CELERY_BROKER_URL="redis://redis:6379/0",
        STATIC_PUBLISH_BROKER_HEALTHCHECK_TIMEOUT=0.25,
    )
    @patch("ai_author_forum.static_publish.health.Connection")
    def test_broker_readiness_bounds_connect_and_socket_io(self, connection):
        connection.side_effect = TimeoutError

        healthy, message = broker_health()

        self.assertFalse(healthy)
        self.assertIn("TimeoutError", message)
        connection.assert_called_once_with(
            "redis://redis:6379/0",
            connect_timeout=0.25,
            transport_options={
                "socket_connect_timeout": 0.25,
                "socket_timeout": 0.25,
            },
        )

    @override_settings(READER_INTERACTIONS_ENABLED=True)
    @patch("ai_author_forum.reader_interactions.health.reader_dependency_health")
    def test_reader_dependencies_gate_readiness_when_reader_is_enabled(
        self, reader_dependency_health
    ):
        self.create_active_release()
        reader_dependency_health.return_value = {
            "reader_rate_limit": (
                False,
                "reader rate limiting unavailable: ConnectionError",
            )
        }

        response = self.client.get(reverse("readyz"))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["reader_rate_limit"]["ok"])

    def test_management_command_reports_ready_release(self):
        self.create_active_release()

        call_command("check_static_publish_health", skip_broker=True, verbosity=0)

    def test_management_command_fails_without_release(self):
        with self.assertRaises(CommandError):
            call_command("check_static_publish_health", skip_broker=True, verbosity=0)
