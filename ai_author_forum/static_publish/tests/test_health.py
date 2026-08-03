import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

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

    def test_liveness_checks_database_without_requiring_a_release(self):
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

    def test_management_command_reports_ready_release(self):
        self.create_active_release()

        call_command("check_static_publish_health", skip_broker=True, verbosity=0)

    def test_management_command_fails_without_release(self):
        with self.assertRaises(CommandError):
            call_command("check_static_publish_health", skip_broker=True, verbosity=0)
