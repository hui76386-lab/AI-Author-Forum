"""Short-lived, fail-closed grants for activated protected PDFs."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import article_output_path
from ai_author_forum.reader_access.models import ProtectedArtifact, ProtectedManifest
from ai_author_forum.reader_access.protected_storage import (
    get_protected_storage,
    validate_object_key,
)
from ai_author_forum.static_publish.models import StaticManifest

from .capabilities import get_effective_capabilities
from .crypto import security_fingerprint, token_digest
from .models import (
    DownloadGrant,
    IdempotencyRecord,
    InteractionOutbox,
    ReaderActionEvent,
    ReaderIdentity,
)
from .rate_limits import RateLimitUnavailable, RedisAtomicRateLimiter


class DownloadError(RuntimeError):
    code = "download_unavailable"
    status = 503
    retry_after = None


class DownloadNotAllowed(DownloadError):
    code = "download_not_allowed"
    status = 403


class DownloadNotFound(DownloadError):
    code = "download_not_found"
    status = 404


class DownloadGrantExpired(DownloadError):
    code = "download_grant_expired"
    status = 410


class DownloadRateLimited(DownloadError):
    code = "rate_limited"
    status = 429

    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after))
        super().__init__("Download grant rate limit exceeded.")


class DownloadIdempotencyConflict(DownloadError):
    code = "idempotency_conflict"
    status = 409


@dataclass(frozen=True)
class IssuedDownload:
    grant_public_id: UUID
    expires_at: object
    download_url: str
    release_version: str


@dataclass(frozen=True)
class InternalDownload:
    x_accel_redirect: str
    filename: str
    byte_size: int


def _grant_token(grant_public_id):
    digest = hmac.new(
        settings.READER_TOKEN_PEPPER.encode(),
        f"download:{grant_public_id}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _active_artifact(article_public_id):
    article = ArticlePage.objects.filter(public_id=article_public_id).first()
    if article is None or article.approved_version_id is None:
        raise DownloadNotFound("Article is unavailable.")
    manifest = StaticManifest.objects.select_for_update().filter(is_active=True).first()
    if manifest is None:
        raise DownloadNotAllowed("No public release is active.")
    if (
        article.publication_status != ArticlePage.PublicationStatus.PUBLISHED
        or article.published_version != manifest.version
        or article_output_path(article)
        not in {item.get("path") for item in manifest.files}
    ):
        raise DownloadNotAllowed("Article is not part of the active public release.")
    protected = ProtectedManifest.objects.filter(
        static_manifest=manifest,
        validation_status=ProtectedManifest.ValidationStatus.ACTIVATED,
    ).first()
    if protected is None:
        raise DownloadNotAllowed("No protected release is active.")
    artifact = ProtectedArtifact.objects.filter(
        article_public_id=article.public_id,
        release_version=manifest.version,
        approved_revision_id=article.approved_version_id,
        status=ProtectedArtifact.Status.ACTIVATED,
    ).first()
    if artifact is None:
        raise DownloadNotAllowed("The active article has no protected PDF.")
    matching_file = next(
        (
            item
            for item in protected.files
            if item.get("article_public_id") == str(article.public_id)
            and int(item.get("approved_revision_id", 0)) == article.approved_version_id
            and item.get("object_key") == artifact.object_key
            and item.get("sha256") == artifact.sha256
            and int(item.get("byte_size", -1)) == artifact.byte_size
        ),
        None,
    )
    if matching_file is None:
        raise DownloadNotAllowed("Protected manifest does not match the artifact.")
    return article, manifest, artifact


def _capability_allows(article_public_id, reader, manifest, artifact):
    capabilities = get_effective_capabilities(
        article_public_id,
        session_context=type("DownloadSession", (), {"reader": reader})(),
    )
    if (
        not capabilities.can_download
        or capabilities.service_degraded
        or capabilities.applying
        or capabilities.active_release != manifest.version
    ):
        raise DownloadNotAllowed("PDF downloads are disabled.")
    from .models import ArticleCapabilityProjection

    projection = ArticleCapabilityProjection.objects.filter(
        article_public_id=article_public_id
    ).first()
    if (
        projection is None
        or projection.protected_artifact_public_id != artifact.public_id
        or projection.approved_revision_id != artifact.approved_revision_id
    ):
        raise DownloadNotAllowed("PDF capability projection is not current.")


def _response_for_grant(grant, artifact, *, storage):
    if settings.READER_PRIVATE_STORAGE_BACKEND == "s3":
        url = storage.presigned_download(
            artifact.object_key,
            expires_seconds=max(
                1, int((grant.expires_at - timezone.now()).total_seconds())
            ),
            filename=f"article-{grant.article_public_id}.pdf",
        )
    else:
        token = _grant_token(grant.public_id)
        if not hmac.compare_digest(grant.token_hash or "", token_digest(token)):
            raise DownloadError("Download grant token state is invalid.")
        url = f"/reader-api/v1/downloads/{grant.public_id}/{token}/"
    return IssuedDownload(
        grant.public_id,
        grant.expires_at,
        url,
        grant.release_version,
    )


def issue_download_grant(
    *,
    article_public_id,
    reader,
    idempotency_key,
    request_hash,
    limiter=None,
    storage=None,
    remote_address="0.0.0.0",
):
    if not settings.READER_PDF_GRANTS_ENABLED:
        raise DownloadNotAllowed("PDF grants are disabled.")
    if not idempotency_key or len(idempotency_key) > 255:
        raise DownloadIdempotencyConflict("Idempotency-Key is required.")
    article_public_id = UUID(str(article_public_id))
    storage = storage or get_protected_storage()
    now = timezone.now()
    key_hash = security_fingerprint("download", idempotency_key)
    with transaction.atomic(using="default"):
        article, manifest, artifact = _active_artifact(article_public_id)
        if not storage.exists(artifact.object_key):
            raise DownloadError("Protected PDF storage is unavailable.")
        with transaction.atomic(using="interactions"):
            locked_reader = ReaderIdentity.objects.select_for_update().get(pk=reader.pk)
            if locked_reader.status != ReaderIdentity.Status.ACTIVE:
                raise DownloadNotAllowed("Reader is not active.")
            previous = IdempotencyRecord.objects.filter(
                reader=locked_reader,
                scope=f"download.grant:{article_public_id}",
                key_hash=key_hash,
            ).first()
            if previous:
                if not hmac.compare_digest(previous.request_hash, request_hash):
                    raise DownloadIdempotencyConflict(
                        "Idempotency key payload mismatch."
                    )
                grant = DownloadGrant.objects.filter(
                    public_id=previous.response_body.get("grant_public_id"),
                    reader=locked_reader,
                ).first()
                if grant is None or grant.expires_at <= now:
                    raise DownloadGrantExpired("Download grant has expired.")
                if (
                    grant.release_version != manifest.version
                    or grant.artifact_public_id != artifact.public_id
                ):
                    raise DownloadNotAllowed(
                        "Download grant no longer matches the active release."
                    )
                _capability_allows(article_public_id, locked_reader, manifest, artifact)
                return _response_for_grant(grant, artifact, storage=storage)

            _capability_allows(article_public_id, locked_reader, manifest, artifact)
            try:
                ip_key = security_fingerprint("reader-download-ip", remote_address)
                decision = (
                    limiter
                    or RedisAtomicRateLimiter(
                        settings.READER_PDF_GRANT_REDIS_URL,
                        namespace="reader-download",
                    )
                ).check_windowed(
                    (
                        (
                            "article-hour",
                            f"{locked_reader.public_id}:{article_public_id}",
                            settings.READER_DOWNLOAD_ARTICLE_HOURLY_LIMIT,
                            3600,
                        ),
                        (
                            "reader-day",
                            str(locked_reader.public_id),
                            settings.READER_DOWNLOAD_DAILY_LIMIT,
                            86400,
                        ),
                        (
                            "ip-hour",
                            ip_key,
                            settings.READER_DOWNLOAD_IP_HOURLY_LIMIT,
                            3600,
                        ),
                    )
                )
            except RateLimitUnavailable as exc:
                raise DownloadError("Download rate limiting is unavailable.") from exc
            if not decision.allowed:
                raise DownloadRateLimited(decision.retry_after)
            expires_at = now + timedelta(
                seconds=settings.READER_DOWNLOAD_GRANT_TTL_SECONDS
            )
            grant = DownloadGrant.objects.create(
                article_public_id=article_public_id,
                reader=locked_reader,
                release_version=manifest.version,
                artifact_public_id=artifact.public_id,
                expires_at=expires_at,
            )
            if settings.READER_PRIVATE_STORAGE_BACKEND == "filesystem":
                token = _grant_token(grant.public_id)
                grant.token_hash = token_digest(token)
                grant.save(update_fields=("token_hash",))
            IdempotencyRecord.objects.create(
                reader=locked_reader,
                scope=f"download.grant:{article_public_id}",
                key_hash=key_hash,
                request_hash=request_hash,
                response_status=201,
                response_body={"grant_public_id": str(grant.public_id)},
                expires_at=expires_at,
            )
            event = ReaderActionEvent.objects.create(
                event_type=ReaderActionEvent.EventType.DOWNLOAD_GRANTED,
                article_public_id=article_public_id,
                reader_public_id=locked_reader.public_id,
                outcome="issued",
            )
            InteractionOutbox.objects.create(
                event_type="reader.download.granted",
                aggregate_type="download_grant",
                aggregate_id=str(grant.public_id),
                aggregate_version=1,
                payload={
                    "event_id": str(event.event_id),
                    "grant_public_id": str(grant.public_id),
                    "article_public_id": str(article_public_id),
                    "release_version": manifest.version,
                    "expires_at": expires_at.isoformat(),
                },
            )
            return _response_for_grant(grant, artifact, storage=storage)


def consume_filesystem_grant(
    *, grant_public_id, token, reader, storage=None, consume=True
):
    if settings.READER_PRIVATE_STORAGE_BACKEND != "filesystem":
        raise DownloadNotFound("Filesystem download endpoint is unavailable.")
    if not settings.READER_PDF_GRANTS_ENABLED:
        raise DownloadNotAllowed("PDF grants are disabled.")
    storage = storage or get_protected_storage()
    now = timezone.now()
    with transaction.atomic(using="default"):
        with transaction.atomic(using="interactions"):
            grant = (
                DownloadGrant.objects.select_for_update()
                .filter(public_id=grant_public_id, reader=reader)
                .first()
            )
            if grant is None or not hmac.compare_digest(
                grant.token_hash or "", token_digest(str(token or ""))
            ):
                raise DownloadNotFound("Download grant was not found.")
            if grant.status != DownloadGrant.Status.ISSUED or grant.expires_at <= now:
                if (
                    grant.status == DownloadGrant.Status.ISSUED
                    and grant.expires_at <= now
                ):
                    grant.status = DownloadGrant.Status.EXPIRED
                    grant.save(update_fields=("status",))
                raise DownloadGrantExpired(
                    "Download grant has expired or was consumed."
                )
            if reader.status != ReaderIdentity.Status.ACTIVE:
                raise DownloadNotAllowed("Reader is not active.")
            article, manifest, artifact = _active_artifact(grant.article_public_id)
            _capability_allows(grant.article_public_id, reader, manifest, artifact)
            if (
                grant.release_version != manifest.version
                or grant.artifact_public_id != artifact.public_id
                or not storage.exists(artifact.object_key)
            ):
                raise DownloadNotAllowed(
                    "Download grant no longer matches active state."
                )
            if consume:
                grant.status = DownloadGrant.Status.CONSUMED
                grant.consumed_at = now
                grant.save(update_fields=("status", "consumed_at"))
                event = ReaderActionEvent.objects.create(
                    event_type=ReaderActionEvent.EventType.DOWNLOAD_STARTED,
                    article_public_id=grant.article_public_id,
                    reader_public_id=reader.public_id,
                    outcome="started",
                )
                InteractionOutbox.objects.create(
                    event_type="reader.download.started",
                    aggregate_type="download_grant",
                    aggregate_id=str(grant.public_id),
                    aggregate_version=2,
                    payload={
                        "event_id": str(event.event_id),
                        "grant_public_id": str(grant.public_id),
                        "article_public_id": str(grant.article_public_id),
                        "release_version": grant.release_version,
                    },
                )
    key = validate_object_key(artifact.object_key)
    filename = f"article-{article.public_id}.pdf"
    return InternalDownload(
        settings.READER_PDF_X_ACCEL_PREFIX.rstrip("/") + "/" + key,
        quote(filename, safe=""),
        artifact.byte_size,
    )
