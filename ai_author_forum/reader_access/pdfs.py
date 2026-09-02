"""Frozen PDF rendering, validation, protected manifests, and paired activation."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import time
from base64 import b64encode
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from uuid import UUID

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.loader import render_to_string

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import (
    APPROVED_REVIEW_STATUSES,
    article_output_path,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.models import StaticManifest

from .models import (
    ArticleInteractionPolicy,
    JournalInteractionPolicy,
    ProtectedArtifact,
    ProtectedManifest,
)
from .protected_storage import get_protected_storage, object_sha256

logger = logging.getLogger(__name__)


class PdfBuildError(RuntimeError):
    code = "pdf_build_failed"


class PdfInputMismatch(PdfBuildError):
    code = "pdf_input_mismatch"


class PdfValidationError(PdfBuildError):
    code = "pdf_validation_failed"


class ProtectedManifestError(PdfBuildError):
    code = "protected_manifest_invalid"


class PdfBuildInProgress(PdfBuildError):
    code = "pdf_build_in_progress"


@dataclass(frozen=True)
class FrozenPdfInput:
    article_public_id: UUID
    approved_revision_id: int
    release_version: str
    locale: str
    title: str
    authors: str
    journal: str
    published_at: str
    body_html: str
    ai_statement: str
    responsibility_statement: str
    copyright_statement: str
    policy_version: int
    frontend_assets_sha256: str
    canonical_url: str
    generated_at: str


@dataclass(frozen=True)
class PdfValidation:
    byte_size: int
    sha256: str
    page_count: int
    font_count: int


def artifact_object_key(*, release_version, article_public_id, locale):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", str(release_version or "")):
        raise ValidationError("Invalid PDF release version.")
    locale = str(locale or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9-]{2,32}", locale):
        raise ValidationError("Invalid PDF locale.")
    return PurePosixPath(
        "protected",
        "releases",
        str(release_version),
        "articles",
        str(UUID(str(article_public_id))),
        locale,
        "article.pdf",
    ).as_posix()


def _manifest_contains_article(manifest, article):
    return article_output_path(article) in {
        item.get("path") for item in (manifest.files or []) if item.get("path")
    }


def _download_policy(article):
    journal_policy = JournalInteractionPolicy.objects.filter(
        journal_id=article.primary_journal_id
    ).first()
    article_policy = ArticleInteractionPolicy.objects.filter(article=article).first()
    enabled = journal_policy.default_pdf_download_enabled if journal_policy else True
    if article_policy and article_policy.pdf_download_policy != "inherit":
        enabled = article_policy.pdf_download_policy == "enabled"
    journal_version = journal_policy.version if journal_policy else 0
    article_version = article_policy.version if article_policy else 0
    return bool(enabled), (int(journal_version) << 32) + int(article_version)


def _download_policy_enabled(article):
    return _download_policy(article)[0]


def _embed_frozen_images(body_html):
    soup = BeautifulSoup(str(body_html), "html.parser")
    media_root = Path(settings.MEDIA_ROOT).resolve()
    total_bytes = 0
    for image in soup.find_all("img"):
        source = str(image.get("src") or "").strip()
        if source.startswith("data:image/"):
            image.attrs.pop("srcset", None)
            continue
        parsed = urlparse(source)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/media/"):
            raise PdfInputMismatch("PDF body contains a non-frozen image source.")
        relative = PurePosixPath(unquote(parsed.path).removeprefix("/media/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise PdfInputMismatch("PDF image path is invalid.")
        candidate = (media_root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(media_root)
        except ValueError as exc:
            raise PdfInputMismatch("PDF image escaped the media root.") from exc
        if not candidate.is_file():
            raise PdfInputMismatch("PDF image is missing from the frozen input.")
        total_bytes += candidate.stat().st_size
        if total_bytes > settings.READER_PDF_MAX_BYTES:
            raise PdfInputMismatch("Frozen PDF images exceed the configured limit.")
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or ""
        if not content_type.startswith("image/"):
            raise PdfInputMismatch("PDF image has an unsupported content type.")
        image["src"] = f"data:{content_type};base64,{b64encode(data).decode()}"
        image.attrs.pop("srcset", None)
        image.attrs.pop("loading", None)
    return str(soup)


def freeze_pdf_input(
    *, article_public_id, release_version, approved_revision_id, locale
):
    article = (
        ArticlePage.objects.select_related(
            "approved_version", "primary_journal", "locale"
        )
        .filter(public_id=article_public_id)
        .first()
    )
    if article is None or article.approved_version_id is None:
        raise PdfInputMismatch("Article has no approved revision.")
    if article.approved_version_id != int(approved_revision_id):
        raise PdfInputMismatch("Approved revision does not match the artifact request.")
    if article.review_status not in {
        ArticlePage.ReviewStatus.APPROVED,
        ArticlePage.ReviewStatus.PUBLISHED,
    }:
        raise PdfInputMismatch("Draft or rejected articles cannot produce PDFs.")
    manifest = StaticManifest.objects.filter(version=release_version).first()
    if manifest is None or not _manifest_contains_article(manifest, article):
        raise PdfInputMismatch("Article is not in the requested public manifest.")
    download_enabled, policy_version = _download_policy(article)
    if not download_enabled:
        raise PdfInputMismatch("PDF policy is disabled for this article.")
    frozen = article.approved_version.as_object()
    if frozen.pk != article.pk:
        raise PdfInputMismatch("Approved revision belongs to another article.")
    frozen_locale = frozen.locale.language_code
    if str(locale).lower() != frozen_locale.lower():
        raise PdfInputMismatch("Requested locale does not match the approved revision.")
    canonical_url = (
        settings.READER_PUBLIC_BASE_URL.rstrip("/") + article.get_absolute_url()
    )
    asset_payload = json.dumps(
        [
            item
            for item in (manifest.files or [])
            if str(item.get("path", "")).startswith(("static/", "assets/"))
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    generated_at = manifest.created_at.isoformat()
    return FrozenPdfInput(
        article_public_id=article.public_id,
        approved_revision_id=article.approved_version_id,
        release_version=str(release_version),
        locale=str(locale),
        title=str(frozen.title),
        authors=str(frozen.authors),
        journal=str(frozen.primary_journal),
        published_at=(
            article.first_published_at.isoformat() if article.first_published_at else ""
        ),
        body_html=_embed_frozen_images(str(frozen.body)),
        ai_statement=str(frozen.ai_contribution_statement or ""),
        responsibility_statement=str(frozen.responsibility_statement or ""),
        copyright_statement=(
            f"Copyright {manifest.created_at.year} {frozen.authors}. "
            "Use is subject to the article's published license and journal terms."
        ),
        policy_version=policy_version,
        frontend_assets_sha256=hashlib.sha256(asset_payload).hexdigest(),
        canonical_url=canonical_url,
        generated_at=generated_at,
    )


def request_pdf_artifact(
    *, article_public_id, release_version, approved_revision_id, locale="en"
):
    frozen = freeze_pdf_input(
        article_public_id=article_public_id,
        release_version=release_version,
        approved_revision_id=approved_revision_id,
        locale=locale,
    )
    key = artifact_object_key(
        release_version=release_version,
        article_public_id=article_public_id,
        locale=locale,
    )
    artifact, created = ProtectedArtifact.objects.get_or_create(
        article_public_id=frozen.article_public_id,
        release_version=frozen.release_version,
        locale=frozen.locale,
        defaults={
            "approved_revision_id": frozen.approved_revision_id,
            "object_key": key,
        },
    )
    if not created and artifact.approved_revision_id != frozen.approved_revision_id:
        raise PdfInputMismatch("Existing artifact points to another revision.")
    if artifact.status == ProtectedArtifact.Status.FAILED:
        artifact.status = ProtectedArtifact.Status.REQUESTED
        artifact.error_code = ""
        artifact.save(update_fields=("status", "error_code", "updated_at"))
    return artifact


class PlaywrightPdfRenderer:
    def render(self, frozen):
        from playwright.sync_api import sync_playwright

        html = render_to_string(
            "reader_interactions/pdf/article.html", {"document": frozen}
        )
        timeout_ms = int(settings.READER_PDF_RENDER_TIMEOUT_SECONDS) * 1000
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=(
                    "--disable-dev-shm-usage",
                    "--disable-background-networking",
                    "--disable-component-update",
                ),
            )
            try:
                page = browser.new_page(locale=frozen.locale)
                page.set_default_timeout(timeout_ms)
                page.route("**/*", lambda route: route.abort())
                page.set_content(
                    html, wait_until="domcontentloaded", timeout=timeout_ms
                )
                return page.pdf(
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template=(
                        '<div style="font-size:8px;width:100%;text-align:center">'
                        '<span class="pageNumber"></span> / '
                        '<span class="totalPages"></span></div>'
                    ),
                    margin={
                        "top": "16mm",
                        "right": "14mm",
                        "bottom": "18mm",
                        "left": "14mm",
                    },
                )
            finally:
                browser.close()


def validate_pdf_bytes(data, frozen):
    data = bytes(data)
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
        raise PdfValidationError("Renderer output is not a complete PDF.")
    if not 100 <= len(data) <= settings.READER_PDF_MAX_BYTES:
        raise PdfValidationError("PDF byte size is outside the configured limit.")
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        if not reader.pages:
            raise PdfValidationError("PDF contains no pages.")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        normalized = " ".join(text.split())
        # PDF text extraction is not reliable for every CJK font mapping. The
        # UUID and release are the machine-verifiable traceability anchors;
        # the title remains visible in the rendered document but is not used
        # as a hard validation gate when extraction drops its glyph mapping.
        required = (str(frozen.article_public_id), frozen.release_version)
        if any(" ".join(value.split()) not in normalized for value in required):
            raise PdfValidationError("PDF is missing required traceability text.")
        fonts = set()
        embedded_fonts = set()
        for page in reader.pages:
            resources = page.get("/Resources") or {}
            if hasattr(resources, "get_object"):
                resources = resources.get_object()
            font_resources = resources.get("/Font") or {}
            if hasattr(font_resources, "get_object"):
                font_resources = font_resources.get_object()
            for name, font_reference in font_resources.items():
                fonts.add(str(name))
                font = (
                    font_reference.get_object()
                    if hasattr(font_reference, "get_object")
                    else font_reference
                )
                candidates = [font]
                descendants = font.get("/DescendantFonts") or []
                candidates.extend(
                    item.get_object() if hasattr(item, "get_object") else item
                    for item in descendants
                )
                for candidate in candidates:
                    descriptor = candidate.get("/FontDescriptor") or {}
                    if hasattr(descriptor, "get_object"):
                        descriptor = descriptor.get_object()
                    if any(
                        descriptor.get(key) is not None
                        for key in ("/FontFile", "/FontFile2", "/FontFile3")
                    ):
                        embedded_fonts.add(str(name))
        if not fonts or not embedded_fonts:
            raise PdfValidationError("PDF has no embedded font resources.")
    except PdfValidationError:
        raise
    except Exception as exc:
        raise PdfValidationError("PDF parser rejected renderer output.") from exc
    return PdfValidation(
        len(data), object_sha256(data), len(reader.pages), len(embedded_fonts)
    )


def render_artifact(artifact_public_id, *, renderer=None, storage=None):
    renderer = renderer or PlaywrightPdfRenderer()
    storage = storage or get_protected_storage()
    with transaction.atomic(using="default"):
        artifact = (
            ProtectedArtifact.objects.select_for_update()
            .filter(public_id=artifact_public_id)
            .first()
        )
        if artifact is None:
            raise PdfBuildError("PDF artifact does not exist.")
        if artifact.status in {
            ProtectedArtifact.Status.READY,
            ProtectedArtifact.Status.ACTIVATED,
        }:
            return artifact
        if artifact.status in {
            ProtectedArtifact.Status.RENDERING,
            ProtectedArtifact.Status.VALIDATING,
        }:
            raise PdfBuildInProgress("PDF artifact is already being rendered.")
        if artifact.status != ProtectedArtifact.Status.REQUESTED:
            raise PdfBuildError("PDF artifact is not eligible for rendering.")
        artifact.status = ProtectedArtifact.Status.RENDERING
        artifact.error_code = ""
        artifact.save(update_fields=("status", "error_code", "updated_at"))
    AuditLog.record(
        action=AuditAction.PUBLISH,
        status=AuditStatus.STARTED,
        target=artifact,
        message="Protected PDF render started",
        metadata={
            "article_public_id": str(artifact.article_public_id),
            "release_version": artifact.release_version,
            "approved_revision_id": artifact.approved_revision_id,
        },
    )
    try:
        frozen = freeze_pdf_input(
            article_public_id=artifact.article_public_id,
            release_version=artifact.release_version,
            approved_revision_id=artifact.approved_revision_id,
            locale=artifact.locale,
        )
        data = renderer.render(frozen)
        artifact.status = ProtectedArtifact.Status.VALIDATING
        artifact.save(update_fields=("status", "updated_at"))
        validation = validate_pdf_bytes(data, frozen)
        stored = storage.put_bytes(artifact.object_key, data)
        if (
            stored.byte_size != validation.byte_size
            or stored.sha256 != validation.sha256
        ):
            raise PdfValidationError("Stored PDF checksum does not match validation.")
        artifact.mime_type = "application/pdf"
        artifact.byte_size = validation.byte_size
        artifact.sha256 = validation.sha256
        artifact.renderer_version = settings.READER_PDF_RENDERER_VERSION
        artifact.status = ProtectedArtifact.Status.READY
        artifact.save(
            update_fields=(
                "mime_type",
                "byte_size",
                "sha256",
                "renderer_version",
                "status",
                "updated_at",
            )
        )
    except Exception as exc:
        artifact.status = ProtectedArtifact.Status.FAILED
        artifact.error_code = getattr(exc, "code", type(exc).__name__.lower())[:64]
        artifact.save(update_fields=("status", "error_code", "updated_at"))
        AuditLog.record(
            action=AuditAction.PUBLISH,
            status=AuditStatus.FAILURE,
            target=artifact,
            message="Protected PDF render failed",
            metadata={
                "article_public_id": str(artifact.article_public_id),
                "release_version": artifact.release_version,
                "error_code": artifact.error_code,
            },
        )
        raise
    AuditLog.record(
        action=AuditAction.PUBLISH,
        status=AuditStatus.SUCCESS,
        target=artifact,
        message="Protected PDF render ready",
        metadata={
            "article_public_id": str(artifact.article_public_id),
            "release_version": artifact.release_version,
            "approved_revision_id": artifact.approved_revision_id,
            "sha256": artifact.sha256,
            "byte_size": artifact.byte_size,
        },
    )
    return artifact


def _manifest_article_ids(static_manifest):
    ids = set()
    for target in (static_manifest.metadata or {}).get("targets", ()):
        if target.get("target_type") != "article_page":
            continue
        for value in (target.get("dependencies") or {}).get("article_ids", ()):
            if str(value).isdigit():
                ids.add(int(value))
    return ids


def required_pdf_articles(static_manifest):
    return [
        article
        for article in ArticlePage.objects.filter(
            pk__in=_manifest_article_ids(static_manifest),
            approved_version__isnull=False,
            review_status__in=APPROVED_REVIEW_STATUSES,
        ).select_related("primary_journal", "locale")
        if _download_policy_enabled(article)
    ]


def build_protected_manifest(static_manifest, *, storage=None):
    storage = storage or get_protected_storage()
    existing = ProtectedManifest.objects.filter(static_manifest=static_manifest).first()
    if existing and existing.validation_status in {
        ProtectedManifest.ValidationStatus.VALIDATED,
        ProtectedManifest.ValidationStatus.ACTIVATED,
    }:
        for item in existing.files:
            stored = storage.metadata(item["object_key"])
            if stored.byte_size != item["byte_size"] or stored.sha256 != item["sha256"]:
                raise ProtectedManifestError("Protected PDF checksum mismatch.")
        return existing
    if existing:
        raise ProtectedManifestError("Existing protected manifest is not valid.")
    files = []
    artifacts = []
    for article in required_pdf_articles(static_manifest):
        locale = article.locale.language_code
        artifact = ProtectedArtifact.objects.filter(
            article_public_id=article.public_id,
            release_version=static_manifest.version,
            approved_revision_id=article.approved_version_id,
            locale=locale,
            status__in=(
                ProtectedArtifact.Status.READY,
                ProtectedArtifact.Status.ACTIVATED,
            ),
        ).first()
        if artifact is None:
            raise ProtectedManifestError(
                f"Required PDF is not ready for article {article.public_id}."
            )
        stored = storage.metadata(artifact.object_key)
        if stored.byte_size != artifact.byte_size or stored.sha256 != artifact.sha256:
            raise ProtectedManifestError("Protected PDF checksum mismatch.")
        artifacts.append(artifact)
        files.append(
            {
                "article_public_id": str(article.public_id),
                "approved_revision_id": article.approved_version_id,
                "locale": locale,
                "object_key": artifact.object_key,
                "mime_type": artifact.mime_type,
                "byte_size": artifact.byte_size,
                "sha256": artifact.sha256,
            }
        )
    files.sort(key=lambda item: (item["article_public_id"], item["locale"]))
    encoded = json.dumps(
        {"version": static_manifest.version, "files": files},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    manifest_key = PurePosixPath(
        "protected", "releases", static_manifest.version, "protected-manifest.json"
    ).as_posix()
    storage.put_bytes(manifest_key, encoded)
    protected = ProtectedManifest.objects.create(
        static_manifest=static_manifest,
        version=static_manifest.version,
        files=files,
        sha256=digest,
        validation_status=ProtectedManifest.ValidationStatus.VALIDATED,
    )
    return protected


def request_protected_release(static_manifest):
    artifacts = []
    for article in required_pdf_articles(static_manifest):
        artifact = request_pdf_artifact(
            article_public_id=article.public_id,
            release_version=static_manifest.version,
            approved_revision_id=article.approved_version_id,
            locale=article.locale.language_code,
        )
        artifacts.append(artifact)
    from .tasks import render_pdf

    for artifact in artifacts:
        if artifact.status == ProtectedArtifact.Status.REQUESTED:
            render_pdf.apply_async(
                args=[str(artifact.public_id)],
                queue="reader_pdf",
                argsrepr="(<artifact>,)",
            )
    return artifacts


def wait_for_protected_release(static_manifest):
    artifacts = request_protected_release(static_manifest)
    deadline = time.monotonic() + settings.READER_PDF_BUILD_WAIT_SECONDS
    pending_ids = {artifact.pk for artifact in artifacts}
    while pending_ids:
        statuses = dict(
            ProtectedArtifact.objects.filter(pk__in=pending_ids).values_list(
                "pk", "status"
            )
        )
        failed = [
            artifact_id
            for artifact_id, status in statuses.items()
            if status == ProtectedArtifact.Status.FAILED
        ]
        if failed:
            raise ProtectedManifestError("A required PDF artifact failed validation.")
        pending_ids = {
            artifact_id
            for artifact_id, status in statuses.items()
            if status
            not in {
                ProtectedArtifact.Status.READY,
                ProtectedArtifact.Status.ACTIVATED,
            }
        }
        if pending_ids and time.monotonic() >= deadline:
            raise ProtectedManifestError(
                "Timed out waiting for required PDF artifacts."
            )
        if pending_ids:
            time.sleep(max(0.05, settings.READER_PDF_BUILD_POLL_SECONDS))
    return build_protected_manifest(static_manifest)


def activate_protected_manifest(static_manifest, *, storage=None):
    protected = build_protected_manifest(static_manifest, storage=storage)
    if protected.validation_status == ProtectedManifest.ValidationStatus.ACTIVATED:
        return protected
    if protected.validation_status != ProtectedManifest.ValidationStatus.VALIDATED:
        raise ProtectedManifestError("Protected manifest is not validated.")
    with transaction.atomic(using="default"):
        locked = ProtectedManifest.objects.select_for_update().get(pk=protected.pk)
        artifact_ids = [item["article_public_id"] for item in locked.files]
        for artifact in ProtectedArtifact.objects.select_for_update().filter(
            release_version=static_manifest.version,
            article_public_id__in=artifact_ids,
            status=ProtectedArtifact.Status.READY,
        ):
            artifact.status = ProtectedArtifact.Status.ACTIVATED
            artifact.save(update_fields=("status", "updated_at"))
        locked.validation_status = ProtectedManifest.ValidationStatus.ACTIVATED
        locked.save(update_fields=("validation_status",))
        return locked


def require_activated_protected_pair(static_manifest, *, storage=None):
    protected = ProtectedManifest.objects.filter(
        static_manifest=static_manifest,
        validation_status=ProtectedManifest.ValidationStatus.ACTIVATED,
    ).first()
    if protected is None:
        raise ProtectedManifestError("Static release has no activated protected pair.")
    storage = storage or get_protected_storage()
    encoded = json.dumps(
        {"version": protected.version, "files": protected.files},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if object_sha256(encoded) != protected.sha256:
        raise ProtectedManifestError(
            "Activated protected manifest checksum is invalid."
        )
    manifest_key = PurePosixPath(
        "protected", "releases", protected.version, "protected-manifest.json"
    ).as_posix()
    manifest_metadata = storage.metadata(manifest_key)
    if (
        manifest_metadata.byte_size != len(encoded)
        or manifest_metadata.sha256 != protected.sha256
    ):
        raise ProtectedManifestError(
            "Activated protected manifest object is incomplete."
        )
    for item in protected.files:
        metadata = storage.metadata(item["object_key"])
        if metadata.byte_size != item["byte_size"] or metadata.sha256 != item["sha256"]:
            raise ProtectedManifestError("Activated protected artifact is incomplete.")
    return protected
