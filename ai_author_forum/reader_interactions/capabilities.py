"""Fail-closed reader capability projection and Redis deny state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from redis import Redis
from redis.exceptions import RedisError

from .models import ArticleCapabilityProjection

_CLEAR_IF_MATCHES = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class CapabilityStoreUnavailable(RuntimeError):
    pass


class ProjectionConflict(RuntimeError):
    pass


def _canonical_marker(payload):
    return json.dumps(
        {
            "active_release": str(payload.get("active_release") or ""),
            "approved_revision_id": int(payload.get("approved_revision_id") or 0),
            "policy_version": int(payload["policy_version"]),
            "projection_version": int(payload["projection_version"]),
            "comments_mode": str(payload["comments_mode"]),
            "download_enabled": bool(payload["download_enabled"]),
            "protected_artifact_public_id": str(
                payload.get("protected_artifact_public_id") or ""
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class CapabilityDenyStore:
    key_prefix = "{reader-capability}:desired:"

    def __init__(self, client=None):
        self.client = client

    def _client(self):
        if self.client is not None:
            return self.client
        url = settings.READER_CAPABILITY_REDIS_URL
        if not url:
            raise CapabilityStoreUnavailable("Capability Redis is not configured.")
        self.client = Redis.from_url(
            url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        return self.client

    def key(self, article_public_id):
        return f"{self.key_prefix}{article_public_id}"

    def set_many_desired(self, payloads):
        markers = []
        try:
            pipeline = self._client().pipeline(transaction=True)
            for payload in payloads:
                marker = _canonical_marker(payload)
                article_id = str(payload["article_public_id"])
                pipeline.set(self.key(article_id), marker)
                markers.append((article_id, marker))
            pipeline.execute()
        except (RedisError, OSError, ValueError) as exc:
            raise CapabilityStoreUnavailable(
                "Capability deny state could not be written."
            ) from exc
        return markers

    def get_desired(self, article_public_id):
        try:
            raw = self._client().get(self.key(article_public_id))
            if raw is None:
                return None
            payload = json.loads(raw)
            return {
                "active_release": str(payload.get("active_release") or ""),
                "approved_revision_id": int(payload.get("approved_revision_id") or 0),
                "policy_version": int(payload["policy_version"]),
                "projection_version": int(payload["projection_version"]),
                "comments_mode": str(payload["comments_mode"]),
                "download_enabled": bool(payload["download_enabled"]),
                "protected_artifact_public_id": str(
                    payload.get("protected_artifact_public_id") or ""
                ),
            }
        except (
            RedisError,
            OSError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise CapabilityStoreUnavailable(
                "Capability deny state could not be read."
            ) from exc

    def clear_if_matches(self, article_public_id, payload):
        marker = _canonical_marker(payload)
        try:
            return int(
                self._client().eval(
                    _CLEAR_IF_MATCHES,
                    1,
                    self.key(article_public_id),
                    marker,
                )
            )
        except (RedisError, OSError, ValueError) as exc:
            raise CapabilityStoreUnavailable(
                "Capability deny state could not be cleared."
            ) from exc


def _validated_projection_payload(payload):
    try:
        article_public_id = UUID(str(payload["article_public_id"]))
        journal_id = int(payload["journal_id"])
        approved_revision_id = int(payload["approved_revision_id"])
        policy_version = int(payload["policy_version"])
        projection_version = int(payload["projection_version"])
        comments_mode = str(payload["comments_mode"])
        active_release = str(payload.get("active_release") or "")
        artifact = payload.get("protected_artifact_public_id")
        artifact = UUID(str(artifact)) if artifact else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Invalid capability projection payload.") from exc
    if comments_mode not in ArticleCapabilityProjection.CommentsMode.values:
        raise ValidationError("Invalid projected comments mode.")
    if min(journal_id, approved_revision_id, policy_version, projection_version) < 0:
        raise ValidationError("Capability projection versions cannot be negative.")
    return {
        "article_public_id": article_public_id,
        "journal_id": journal_id,
        "active_release": active_release,
        "approved_revision_id": approved_revision_id,
        "comments_mode": comments_mode,
        "download_enabled": bool(payload.get("download_enabled", False)),
        "protected_artifact_public_id": artifact,
        "policy_version": policy_version,
        "projection_version": projection_version,
    }


def apply_capability_projection(payload):
    """Apply an at-least-once projection without allowing version rollback."""

    values = _validated_projection_payload(payload)
    for attempt in range(3):
        try:
            with transaction.atomic(using="interactions"):
                current = (
                    ArticleCapabilityProjection.objects.using("interactions")
                    .select_for_update()
                    .filter(article_public_id=values["article_public_id"])
                    .first()
                )
                if current is None:
                    return (
                        "created",
                        ArticleCapabilityProjection.objects.using(
                            "interactions"
                        ).create(**values, applied_at=timezone.now()),
                    )
                incoming = values["projection_version"]
                if incoming < current.projection_version:
                    return "stale", current
                comparable_fields = tuple(values)
                if incoming == current.projection_version:
                    if all(
                        getattr(current, field) == value
                        for field, value in values.items()
                    ):
                        return "duplicate", current
                    raise ProjectionConflict(
                        "Equal projection versions contain different capability data."
                    )
                for field, value in values.items():
                    setattr(current, field, value)
                current.applied_at = timezone.now()
                current.save(
                    using="interactions",
                    update_fields=(*comparable_fields, "applied_at"),
                )
                return "updated", current
        except IntegrityError:
            if attempt == 2:
                raise
    raise RuntimeError("Capability projection retry exhausted.")


@dataclass(frozen=True)
class EffectiveCapabilities:
    article_public_id: UUID
    active_release: str
    comments_mode: str
    pdf_available: bool
    policy_version: int
    can_comment: bool
    can_download: bool
    share_available: bool
    can_share: bool
    verification_required: bool
    applying: bool
    service_degraded: bool


def get_effective_capabilities(article_public_id, *, session_context=None, store=None):
    article_public_id = UUID(str(article_public_id))
    projection = (
        ArticleCapabilityProjection.objects.using("interactions")
        .filter(article_public_id=article_public_id)
        .first()
    )
    if projection is None:
        return EffectiveCapabilities(
            article_public_id,
            "",
            ArticleCapabilityProjection.CommentsMode.HIDDEN,
            False,
            0,
            False,
            False,
            False,
            False,
            session_context is None,
            True,
            False,
        )

    service_degraded = False
    applying = False
    comments_mode = projection.comments_mode
    download_enabled = projection.download_enabled
    try:
        desired = (store or CapabilityDenyStore()).get_desired(article_public_id)
    except CapabilityStoreUnavailable:
        desired = None
        service_degraded = True
        comments_mode = ArticleCapabilityProjection.CommentsMode.HIDDEN
        download_enabled = False
    if desired and desired["projection_version"] > projection.projection_version:
        applying = True
        comments_mode = ArticleCapabilityProjection.CommentsMode.HIDDEN
        download_enabled = False
    elif desired and desired["projection_version"] == projection.projection_version:
        comments_mode = desired["comments_mode"]
        download_enabled = desired["download_enabled"]

    if not settings.READER_COMMENTS_WRITE_ENABLED and comments_mode == "open":
        comments_mode = ArticleCapabilityProjection.CommentsMode.READ_ONLY
    if not settings.READER_PDF_GRANTS_ENABLED:
        download_enabled = False
    authenticated = session_context is not None
    share_available = bool(
        settings.READER_SHARE_UI_ENABLED and not applying and not service_degraded
    )
    return EffectiveCapabilities(
        article_public_id,
        projection.active_release,
        comments_mode,
        download_enabled,
        projection.policy_version,
        authenticated and comments_mode == "open",
        authenticated and download_enabled,
        share_available,
        authenticated and share_available,
        not authenticated,
        applying,
        service_degraded,
    )
