from uuid import uuid4

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob

from ..models import (
    ControlPlaneOutbox,
    ModerationCommand,
    ProtectedArtifact,
    ProtectedManifest,
)


class ReaderAccessModelTests(TestCase):
    databases = {"default", "interactions"}

    def test_operational_models_are_registered_read_only(self):
        for model in (
            ProtectedArtifact,
            ProtectedManifest,
            ControlPlaneOutbox,
            ModerationCommand,
        ):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(None))
            self.assertFalse(model_admin.has_change_permission(None))
            self.assertFalse(model_admin.has_delete_permission(None))

    def test_control_plane_outbox_is_idempotent_and_uses_controlled_updates(self):
        event_id = uuid4()
        event = ControlPlaneOutbox.objects.create(
            event_id=event_id,
            event_type="policy.changed",
            aggregate_type="article_policy",
            aggregate_id=str(uuid4()),
            aggregate_version=1,
            payload={"version": 1},
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlPlaneOutbox.objects.create(
                event_id=event_id,
                event_type="policy.changed",
                aggregate_type="article_policy",
                aggregate_id=str(uuid4()),
                aggregate_version=1,
            )
        with self.assertRaises(ValidationError):
            ControlPlaneOutbox.objects.filter(pk=event.pk).update(attempts=10)

        self.assertEqual(
            ControlPlaneOutbox.objects.record_attempt(event_id, error="x"), 1
        )
        self.assertEqual(ControlPlaneOutbox.objects.mark_published(event_id), 1)
        self.assertEqual(ControlPlaneOutbox.objects.mark_published(event_id), 0)

    def test_activated_protected_manifest_is_immutable(self):
        job = StaticPublishJob.objects.create()
        static_manifest = StaticManifest.objects.create(
            version="reader-access-test-release",
            job=job,
            files=[],
            metadata={},
        )
        protected = ProtectedManifest.objects.create(
            static_manifest=static_manifest,
            version=static_manifest.version,
            files=[],
            sha256="a" * 64,
        )
        ProtectedManifest.objects.filter(pk=protected.pk).update(
            validation_status=ProtectedManifest.ValidationStatus.VALIDATED
        )
        ProtectedManifest.objects.filter(pk=protected.pk).update(
            validation_status=ProtectedManifest.ValidationStatus.ACTIVATED
        )
        protected.refresh_from_db()

        with self.assertRaises(ValidationError):
            ProtectedManifest.objects.filter(pk=protected.pk).update(
                validation_status=ProtectedManifest.ValidationStatus.FAILED
            )
        with self.assertRaises(ValidationError):
            protected.delete()

    def test_activated_artifact_content_fields_are_immutable(self):
        artifact = ProtectedArtifact.objects.create(
            article_public_id=uuid4(),
            approved_revision_id=12,
            release_version="release-one",
            locale="en",
            object_key="protected/releases/release-one/article/en/article.pdf",
            mime_type="application/pdf",
            byte_size=100,
            sha256="b" * 64,
            renderer_version="reader-pdf/1",
            status=ProtectedArtifact.Status.ACTIVATED,
        )
        artifact.sha256 = "c" * 64

        with self.assertRaises(ValidationError):
            artifact.save()
