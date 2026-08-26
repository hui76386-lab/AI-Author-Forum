import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousFileOperation
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import article_output_path
from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob

from ..models import ProtectedArtifact, ProtectedManifest
from ..pdfs import (
    FrozenPdfInput,
    PdfInputMismatch,
    PlaywrightPdfRenderer,
    ProtectedManifestError,
    activate_protected_manifest,
    build_protected_manifest,
    freeze_pdf_input,
    required_pdf_articles,
    render_artifact,
    validate_pdf_bytes,
)
from ..protected_storage import (
    FILESYSTEM_OBJECT_MODE,
    FileSystemProtectedStorage,
    S3ProtectedStorage,
    object_sha256,
)


class ProtectedStorageTests(SimpleTestCase):
    def test_filesystem_storage_is_private_immutable_and_rejects_traversal(self):
        with TemporaryDirectory() as root:
            storage = FileSystemProtectedStorage(root)
            key = "protected/releases/release-1/articles/id/en/article.pdf"
            metadata = storage.put_bytes(key, b"private-pdf")
            target = Path(root) / key
            self.assertEqual(metadata.sha256, object_sha256(b"private-pdf"))
            self.assertEqual(target.read_bytes(), b"private-pdf")
            self.assertEqual(target.stat().st_mode & 0o777, FILESYSTEM_OBJECT_MODE)
            target.chmod(0o600)
            storage.ensure_readable(key)
            self.assertEqual(target.stat().st_mode & 0o777, FILESYSTEM_OBJECT_MODE)
            target.chmod(0o600)
            self.assertEqual(storage.put_bytes(key, b"private-pdf"), metadata)
            self.assertEqual(target.stat().st_mode & 0o777, FILESYSTEM_OBJECT_MODE)
            with self.assertRaises(FileExistsError):
                storage.put_bytes(key, b"changed")
            with self.assertRaises(SuspiciousFileOperation):
                storage.put_bytes("protected/releases/../escape.pdf", b"x")
            with self.assertRaises(SuspiciousFileOperation):
                storage.put_bytes("media/article.pdf", b"x")

    def test_s3_storage_writes_private_immutable_objects_and_presigns_downloads(self):
        calls = {}

        class FakeS3Client:
            def put_object(self, **kwargs):
                calls["put"] = kwargs

            def head_object(self, **kwargs):
                calls["head"] = kwargs
                return {
                    "ContentLength": len(calls["put"]["Body"]),
                    "Metadata": calls["put"]["Metadata"],
                }

            def generate_presigned_url(self, operation, **kwargs):
                calls["presign"] = (operation, kwargs)
                return "https://objects.example/private-download"

        key = "protected/releases/release-1/articles/id/en/article.pdf"
        storage = S3ProtectedStorage(client=FakeS3Client(), bucket="private-pdfs")
        expected = storage.put_bytes(key, b"private-pdf")

        self.assertEqual(calls["put"]["Bucket"], "private-pdfs")
        self.assertEqual(calls["put"]["Key"], key)
        self.assertEqual(calls["put"]["IfNoneMatch"], "*")
        self.assertEqual(calls["put"]["ContentType"], "application/pdf")
        self.assertEqual(calls["put"]["Metadata"], {"sha256": expected.sha256})
        self.assertEqual(storage.metadata(key), expected)
        self.assertEqual(calls["head"], {"Bucket": "private-pdfs", "Key": key})

        url = storage.presigned_download(
            key, expires_seconds=300, filename="article.pdf"
        )
        self.assertEqual(url, "https://objects.example/private-download")
        operation, kwargs = calls["presign"]
        self.assertEqual(operation, "get_object")
        self.assertEqual(kwargs["ExpiresIn"], 300)
        self.assertEqual(kwargs["Params"]["Bucket"], "private-pdfs")
        self.assertEqual(kwargs["Params"]["Key"], key)
        self.assertEqual(
            kwargs["Params"]["ResponseContentDisposition"],
            'attachment; filename="article.pdf"',
        )


@override_settings(READER_PUBLIC_BASE_URL="https://reader.example")
class PdfLifecycleTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.journal = Journal.objects.create(
            name="PDF Journal",
            slug="pdf-journal",
            az_group="P",
            status=JournalStatus.ACTIVE,
        )
        self.article = ArticlePage(
            title="Frozen PDF Article",
            slug="frozen-pdf-article",
            static_slug="frozen-pdf-article",
            abstract="Abstract",
            body=[("paragraph", "Approved PDF body")],
            authors="PDF Author",
            ai_contribution_statement="AI assisted with language editing.",
            responsibility_statement="Authors retain responsibility.",
            keywords="pdf",
            primary_journal=self.journal,
        )
        Page.get_first_root_node().add_child(instance=self.article)
        self.revision = self.article.save_revision()
        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED,
            approved_version_id=self.revision.pk,
        )
        self.article.refresh_from_db()
        self.locale = self.article.locale.language_code
        self.job = StaticPublishJob.objects.create(
            version="pdf-release-1",
            triggered_by=get_user_model().objects.create_user(
                username="pdf-builder",
                email="pdf-builder@example.com",
                display_name="PDF Builder",
            ),
        )
        self.manifest = StaticManifest.objects.create(
            version="pdf-release-1",
            job=self.job,
            files=[{"path": article_output_path(self.article), "sha256": "a" * 64}],
            metadata={
                "targets": [
                    {
                        "target_type": "article_page",
                        "dependencies": {"article_ids": [self.article.pk]},
                    }
                ]
            },
        )

    def test_freeze_rejects_draft_revision_and_release_mismatch(self):
        with self.assertRaises(PdfInputMismatch):
            freeze_pdf_input(
                article_public_id=self.article.public_id,
                release_version=self.manifest.version,
                approved_revision_id=self.revision.pk + 1,
                locale=self.locale,
            )
        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.DRAFT
        )
        with self.assertRaises(PdfInputMismatch):
            freeze_pdf_input(
                article_public_id=self.article.public_id,
                release_version=self.manifest.version,
                approved_revision_id=self.revision.pk,
                locale=self.locale,
            )
        self.article.refresh_from_db()
        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED
        )
        with self.assertRaises(PdfInputMismatch):
            freeze_pdf_input(
                article_public_id=self.article.public_id,
                release_version="other-release",
                approved_revision_id=self.revision.pk,
                locale=self.locale,
            )

    def test_required_pdf_articles_excludes_article_without_current_approval(self):
        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.DRAFT,
            approved_version_id=None,
        )

        self.assertEqual(required_pdf_articles(self.manifest), [])

    def test_failed_renderer_marks_artifact_failed_without_writing_object(self):
        artifact = ProtectedArtifact.objects.create(
            article_public_id=self.article.public_id,
            approved_revision_id=self.revision.pk,
            release_version=self.manifest.version,
            locale=self.locale,
            object_key=(
                f"protected/releases/{self.manifest.version}/articles/"
                f"{self.article.public_id}/{self.locale}/article.pdf"
            ),
        )
        renderer = type(
            "FailingRenderer",
            (),
            {"render": lambda self, frozen: (_ for _ in ()).throw(TimeoutError())},
        )()
        with TemporaryDirectory() as root:
            storage = FileSystemProtectedStorage(root)
            with self.assertRaises(TimeoutError):
                render_artifact(artifact.public_id, renderer=renderer, storage=storage)
            self.assertFalse(storage.exists(artifact.object_key))
        artifact.refresh_from_db()
        self.assertEqual(artifact.status, ProtectedArtifact.Status.FAILED)
        self.assertEqual(artifact.error_code, "timeouterror")

    def test_manifest_requires_all_files_and_activates_exact_checksums(self):
        with TemporaryDirectory() as root:
            storage = FileSystemProtectedStorage(root)
            with self.assertRaises(ProtectedManifestError):
                build_protected_manifest(self.manifest, storage=storage)
            key = (
                f"protected/releases/{self.manifest.version}/articles/"
                f"{self.article.public_id}/{self.locale}/article.pdf"
            )
            data = b"validated-pdf-placeholder"
            metadata = storage.put_bytes(key, data)
            artifact = ProtectedArtifact.objects.create(
                article_public_id=self.article.public_id,
                approved_revision_id=self.revision.pk,
                release_version=self.manifest.version,
                locale=self.locale,
                object_key=key,
                mime_type="application/pdf",
                byte_size=metadata.byte_size,
                sha256=metadata.sha256,
                renderer_version="test",
                status=ProtectedArtifact.Status.READY,
            )
            protected = activate_protected_manifest(self.manifest, storage=storage)
            artifact.refresh_from_db()
            self.assertEqual(
                protected.validation_status,
                ProtectedManifest.ValidationStatus.ACTIVATED,
            )
            self.assertEqual(artifact.status, ProtectedArtifact.Status.ACTIVATED)
            self.assertNotIn("media/", artifact.object_key)


@skipUnless(os.environ.get("RUN_PDF_RENDERER_TESTS") == "1", "PDF worker test only")
class RealPdfRendererTests(SimpleTestCase):
    def test_real_chromium_pdf_has_header_pages_fonts_text_and_checksum(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not-an-image")

            def log_message(self, format, *args):
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        frozen = FrozenPdfInput(
            article_public_id="ffffffff-1111-4111-8111-111111111111",
            approved_revision_id=42,
            release_version="renderer-release-1",
            locale="en",
            title="Renderer Contract Article",
            authors="Example Author",
            journal="Example Journal",
            published_at="2026-08-17T00:00:00+00:00",
            body_html=(
                "<p>Frozen approved article body.</p>"
                f'<img src="http://127.0.0.1:{server.server_port}/must-not-load.png">'
            ),
            ai_statement="AI language assistance.",
            responsibility_statement="Authors retain responsibility.",
            copyright_statement="Copyright 2026 Example Author.",
            policy_version=7,
            frontend_assets_sha256="a" * 64,
            canonical_url="https://reader.example/articles/renderer/",
            generated_at="2026-08-17T00:00:00+00:00",
        )
        try:
            data = PlaywrightPdfRenderer().render(frozen)
            validation = validate_pdf_bytes(data, frozen)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreaterEqual(validation.page_count, 1)
        self.assertGreaterEqual(validation.font_count, 1)
        self.assertEqual(validation.sha256, object_sha256(data))
        self.assertEqual(requests, [])
