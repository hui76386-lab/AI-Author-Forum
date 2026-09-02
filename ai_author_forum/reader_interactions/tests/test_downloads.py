import json
import logging
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import article_output_path
from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.reader_access.models import ProtectedArtifact, ProtectedManifest
from ai_author_forum.reader_access.protected_storage import FileSystemProtectedStorage
from ai_author_forum.settings.log_filters import RedactReaderBearerPaths
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob

from ..crypto import token_digest
from ..downloads import (
    DownloadGrantExpired,
    DownloadNotAllowed,
    DownloadRateLimited,
    consume_filesystem_grant,
    issue_download_grant,
)
from ..models import (
    ArticleCapabilityProjection,
    DownloadGrant,
    IdempotencyRecord,
    ReaderActionEvent,
    ReaderIdentity,
    ReaderSession,
)
from ..rate_limits import RateLimitDecision


class AllowLimiter:
    def check_windowed(self, dimensions):
        return RateLimitDecision(True)


class DenyLimiter:
    def check_windowed(self, dimensions):
        return RateLimitDecision(False, 29)


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_PDF_GRANTS_ENABLED=True,
    READER_PRIVATE_STORAGE_BACKEND="filesystem",
    READER_DOWNLOAD_GRANT_TTL_SECONDS=300,
)
class DownloadGrantTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.private = TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        self.storage = FileSystemProtectedStorage(self.private.name)
        self.journal = Journal.objects.create(
            name="Download Journal",
            slug="download-journal",
            az_group="D",
            status=JournalStatus.ACTIVE,
        )
        self.article = ArticlePage(
            title="Download Article",
            slug="download-article",
            static_slug="download-article",
            abstract="Abstract",
            body=[("paragraph", "Approved body")],
            authors="Download Author",
            responsibility_statement="Authors retain responsibility.",
            keywords="download",
            primary_journal=self.journal,
        )
        Page.get_first_root_node().add_child(instance=self.article)
        revision = self.article.save_revision()
        self.revision_id = revision.pk
        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED,
            approved_version_id=revision.pk,
            publication_status=ArticlePage.PublicationStatus.PUBLISHED,
            published_version="download-release-1",
        )
        self.article.refresh_from_db()
        job = StaticPublishJob.objects.create(version="download-release-1")
        self.manifest = StaticManifest.objects.create(
            version="download-release-1",
            job=job,
            files=[{"path": article_output_path(self.article), "sha256": "b" * 64}],
            metadata={"targets": []},
            is_active=True,
        )
        key = (
            f"protected/releases/{self.manifest.version}/articles/"
            f"{self.article.public_id}/en/article.pdf"
        )
        metadata = self.storage.put_bytes(key, b"private pdf bytes")
        self.artifact = ProtectedArtifact.objects.create(
            article_public_id=self.article.public_id,
            approved_revision_id=self.revision_id,
            release_version=self.manifest.version,
            locale="en",
            object_key=key,
            mime_type="application/pdf",
            byte_size=metadata.byte_size,
            sha256=metadata.sha256,
            renderer_version="test",
            status=ProtectedArtifact.Status.ACTIVATED,
        )
        ProtectedManifest.objects.create(
            static_manifest=self.manifest,
            version=self.manifest.version,
            files=[
                {
                    "article_public_id": str(self.article.public_id),
                    "approved_revision_id": self.revision_id,
                    "locale": "en",
                    "object_key": key,
                    "mime_type": "application/pdf",
                    "byte_size": metadata.byte_size,
                    "sha256": metadata.sha256,
                }
            ],
            sha256="c" * 64,
            validation_status=ProtectedManifest.ValidationStatus.ACTIVATED,
        )
        self.reader = ReaderIdentity.objects.create(
            email_ciphertext="ciphertext",
            email_lookup_hmac=uuid4().hex + uuid4().hex,
            email_key_version=1,
            email_verified_at=timezone.now(),
            display_name="PDF Reader",
        )
        ArticleCapabilityProjection.objects.create(
            article_public_id=self.article.public_id,
            journal_id=self.journal.pk,
            active_release=self.manifest.version,
            approved_revision_id=self.revision_id,
            comments_mode=ArticleCapabilityProjection.CommentsMode.OPEN,
            download_enabled=True,
            protected_artifact_public_id=self.artifact.public_id,
            policy_version=1,
            projection_version=1,
            applied_at=timezone.now(),
        )
        self.deny_patch = patch(
            "ai_author_forum.reader_interactions.capabilities.CapabilityDenyStore.get_desired",
            return_value=None,
        )
        self.deny_patch.start()
        self.addCleanup(self.deny_patch.stop)

    def issue(self, key="grant-key", limiter=None):
        return issue_download_grant(
            article_public_id=self.article.public_id,
            reader=self.reader,
            idempotency_key=key,
            request_hash="request-hash",
            limiter=limiter or AllowLimiter(),
            storage=self.storage,
        )

    def test_issue_is_short_lived_idempotent_and_stores_no_url_or_token(self):
        issued = self.issue()
        replay = self.issue()
        self.assertEqual(issued.grant_public_id, replay.grant_public_id)
        self.assertEqual(issued.download_url, replay.download_url)
        self.assertLessEqual((issued.expires_at - timezone.now()).total_seconds(), 300)
        grant = DownloadGrant.objects.get(public_id=issued.grant_public_id)
        record = IdempotencyRecord.objects.get(reader=self.reader)
        self.assertTrue(grant.token_hash)
        self.assertNotIn(issued.download_url, json.dumps(record.response_body))
        self.assertNotIn(
            issued.download_url.rsplit("/", 2)[-2], str(record.response_body)
        )
        self.assertEqual(
            ReaderActionEvent.objects.filter(
                event_type=ReaderActionEvent.EventType.DOWNLOAD_GRANTED
            ).count(),
            1,
        )

    def test_rate_limit_policy_projection_and_release_mismatch_fail_closed(self):
        with self.assertRaises(DownloadRateLimited) as limited:
            self.issue(key="limited", limiter=DenyLimiter())
        self.assertEqual(limited.exception.retry_after, 29)
        projection = ArticleCapabilityProjection.objects.get(
            article_public_id=self.article.public_id
        )
        projection.download_enabled = False
        projection.projection_version += 1
        projection.save(update_fields=("download_enabled", "projection_version"))
        with self.assertRaises(DownloadNotAllowed):
            self.issue(key="disabled")
        projection.download_enabled = True
        projection.active_release = "old-release"
        projection.projection_version += 1
        projection.save(
            update_fields=(
                "download_enabled",
                "active_release",
                "projection_version",
            )
        )
        with self.assertRaises(DownloadNotAllowed):
            self.issue(key="old-release")

    def test_expired_and_one_time_tokens_are_rejected(self):
        issued = self.issue()
        grant = DownloadGrant.objects.get(public_id=issued.grant_public_id)
        token = issued.download_url.rstrip("/").rsplit("/", 1)[-1]
        result = consume_filesystem_grant(
            grant_public_id=grant.public_id,
            token=token,
            reader=self.reader,
            storage=self.storage,
        )
        self.assertEqual(result.byte_size, self.artifact.byte_size)
        self.assertTrue(result.x_accel_redirect.startswith("/_protected_pdf/"))
        with self.assertRaises(DownloadGrantExpired):
            consume_filesystem_grant(
                grant_public_id=grant.public_id,
                token=token,
                reader=self.reader,
                storage=self.storage,
            )
        expired = self.issue(key="expired")
        DownloadGrant.objects.filter(public_id=expired.grant_public_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(DownloadGrantExpired):
            consume_filesystem_grant(
                grant_public_id=expired.grant_public_id,
                token=expired.download_url.rstrip("/").rsplit("/", 1)[-1],
                reader=self.reader,
                storage=self.storage,
            )

    def test_api_returns_empty_x_accel_response_and_never_pdf_bytes(self):
        secret = "reader-session-secret"
        now = timezone.now()
        ReaderSession.objects.create(
            reader=self.reader,
            secret_hash=token_digest(secret),
            last_seen_at=now,
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=2),
        )
        client = Client(enforce_csrf_checks=True)
        client.cookies["reader_session"] = secret
        client.get(reverse("reader_session"), secure=True)
        csrf = client.cookies["csrftoken"].value
        with (
            patch(
                "ai_author_forum.reader_interactions.downloads.get_protected_storage",
                return_value=self.storage,
            ),
            patch(
                "ai_author_forum.reader_interactions.downloads.RedisAtomicRateLimiter",
                return_value=AllowLimiter(),
            ),
        ):
            issued = client.post(
                reverse("reader_download_grant", args=[self.article.public_id]),
                data="{}",
                content_type="application/json",
                secure=True,
                HTTP_ORIGIN="https://testserver",
                HTTP_X_CSRFTOKEN=csrf,
                HTTP_IDEMPOTENCY_KEY="api-grant",
            )
            download_url = issued.json()["data"]["download_url"]
            head = client.head(download_url, secure=True)
            response = client.get(download_url, secure=True, HTTP_RANGE="bytes=0-7")
        self.assertEqual(issued.status_code, 201)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertIn("/_protected_pdf/", response["X-Accel-Redirect"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_download_error_log_redacts_bearer_token(self):
        issued = self.issue(key="logged-token")
        token = issued.download_url.rstrip("/").rsplit("/", 1)[-1]
        record = logging.LogRecord(
            "django.request",
            logging.WARNING,
            __file__,
            1,
            "Gone: %s",
            (issued.download_url,),
            None,
        )
        RedactReaderBearerPaths().filter(record)
        output = record.getMessage()
        self.assertNotIn(token, output)
        self.assertIn("<redacted>", output)
