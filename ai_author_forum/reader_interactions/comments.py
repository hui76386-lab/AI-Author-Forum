"""Reader comment, reply, withdrawal, report, and public-list services."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags

from .capabilities import CapabilityDenyStore, CapabilityStoreUnavailable
from .comment_cache import CommentCache, CommentCacheUnavailable
from .crypto import security_fingerprint
from .models import (
    ArticleCapabilityProjection,
    Comment,
    CommentModerationEvent,
    CommentReport,
    IdempotencyRecord,
    InteractionOutbox,
    ReaderIdentity,
)
from .rate_limits import RateLimitUnavailable, RedisAtomicRateLimiter

logger = logging.getLogger(__name__)


class CommentServiceError(RuntimeError):
    code = "service_degraded"
    status = 503


class ArticleNotActive(CommentServiceError):
    code = "article_not_active"
    status = 404


class CommentsClosed(CommentServiceError):
    code = "comments_closed"
    status = 409


class CommentsHidden(CommentServiceError):
    code = "comments_hidden"
    status = 403


class StalePolicy(CommentServiceError):
    code = "stale_policy"
    status = 409


class InvalidComment(CommentServiceError):
    code = "invalid_comment"
    status = 422


class ReplyDepthExceeded(CommentServiceError):
    code = "reply_depth_exceeded"
    status = 422


class CommentNotFound(CommentServiceError):
    code = "comment_not_found"
    status = 404


class NotCommentOwner(CommentServiceError):
    code = "not_comment_owner"
    status = 403


class ReaderSuspended(CommentServiceError):
    code = "reader_suspended"
    status = 403


class AlreadyReported(CommentServiceError):
    code = "already_reported"
    status = 409


class IdempotencyConflict(CommentServiceError):
    code = "idempotency_conflict"
    status = 409


class CommentRateLimited(CommentServiceError):
    code = "rate_limited"
    status = 429

    def __init__(self, retry_after):
        super().__init__(self.code)
        self.retry_after = max(1, int(retry_after))


@dataclass(frozen=True)
class RiskAssessment:
    pending: bool = False
    score: Decimal | None = None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommentWriteResult:
    status: int
    body: dict
    replayed: bool = False


def normalize_comment_body(value):
    body = unicodedata.normalize("NFC", str(value or ""))
    body = body.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    body = strip_tags(body).strip()
    if any(ord(char) < 32 and char != "\n" for char in body):
        raise InvalidComment("Comment contains unsupported control characters.")
    if not 2 <= len(body) <= settings.READER_COMMENT_MAX_CHARS:
        raise InvalidComment("Comment length is outside the allowed range.")
    if len(body.encode("utf-8")) > settings.READER_COMMENT_MAX_BYTES:
        raise InvalidComment("Comment exceeds the UTF-8 byte limit.")
    return body


def assess_comment_risk(body, *, assessor=None):
    if not settings.READER_COMMENT_RISK_ENABLED:
        return RiskAssessment()
    if assessor is None:
        url_count = len(re.findall(r"https?://", body, flags=re.IGNORECASE))
        return (
            RiskAssessment(True, Decimal("0.90000"), ("link_flood",))
            if url_count > 3
            else RiskAssessment(False, Decimal("0.00000"), ())
        )
    try:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(assessor, body)
        try:
            decision = future.result(
                timeout=settings.READER_COMMENT_RISK_TIMEOUT_SECONDS
            )
        except FutureTimeoutError:
            return RiskAssessment(True, None, ("risk_unavailable",))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001 - provider details stay redacted
        return RiskAssessment(True, None, ("risk_unavailable",))
    if not isinstance(decision, RiskAssessment):
        raise InvalidComment("Risk assessor returned an invalid decision.")
    return decision


def _request_hash(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 255:
        raise ValidationError(
            "Idempotency-Key is required and must be at most 255 characters."
        )
    return security_fingerprint("comment-idempotency", value)


def _previous_idempotency(reader, scope, key_hash, request_hash):
    previous = (
        IdempotencyRecord.objects.select_for_update()
        .filter(reader=reader, scope=scope, key_hash=key_hash)
        .first()
    )
    if previous is None:
        return None
    if previous.request_hash != request_hash:
        raise IdempotencyConflict("Idempotency key payload mismatch.")
    return CommentWriteResult(
        previous.response_status,
        previous.response_body,
        replayed=True,
    )


def _record_idempotency(reader, scope, key_hash, request_hash, result):
    IdempotencyRecord.objects.create(
        reader=reader,
        scope=scope,
        key_hash=key_hash,
        request_hash=request_hash,
        response_status=result.status,
        response_body=result.body,
        expires_at=timezone.now() + timedelta(days=1),
    )


def _check_rate_limit(
    reader, *, action, article_public_id, remote_address="0.0.0.0", rate_limiter=None
):
    limiter = rate_limiter or RedisAtomicRateLimiter(namespace="reader-comments")
    reader_key = security_fingerprint("reader-comment-rate", str(reader.public_id))
    ip_key = security_fingerprint("reader-ip-rate", str(remote_address))
    if action == "comment":
        dimensions = [
            (
                "reader",
                reader_key,
                1,
                settings.READER_COMMENT_INTERVAL_SECONDS,
            ),
            (
                "reader-hour",
                reader_key,
                settings.READER_COMMENT_HOURLY_LIMIT,
                3600,
            ),
            (
                "reader-day",
                reader_key,
                settings.READER_COMMENT_DAILY_LIMIT,
                86400,
            ),
            (
                "reader-article-hour",
                f"{reader_key}:{article_public_id}",
                settings.READER_COMMENT_ARTICLE_HOURLY_LIMIT,
                3600,
            ),
            (
                "ip-hour",
                ip_key,
                settings.READER_COMMENT_IP_HOURLY_LIMIT,
                3600,
            ),
        ]
    else:
        dimensions = [
            (
                "reader-day",
                reader_key,
                settings.READER_REPORT_DAILY_LIMIT,
                86400,
            ),
            (
                "ip-day",
                ip_key,
                settings.READER_REPORT_IP_DAILY_LIMIT,
                86400,
            ),
        ]
    try:
        if hasattr(limiter, "check_windowed"):
            decision = limiter.check_windowed(dimensions)
        else:
            decision = limiter.check(
                tuple(
                    (name, value, limit) for name, value, limit, _window in dimensions
                ),
                window_seconds=dimensions[0][3],
            )
    except RateLimitUnavailable as exc:
        raise CommentServiceError("Comment rate limiting is unavailable.") from exc
    if not decision.allowed:
        raise CommentRateLimited(decision.retry_after)


def _lock_projection(
    article_public_id,
    expected_policy_version=None,
    *,
    write=False,
    allow_hidden=False,
):
    projection = (
        ArticleCapabilityProjection.objects.select_for_update()
        .filter(article_public_id=article_public_id)
        .first()
    )
    if projection is None or not projection.active_release:
        raise ArticleNotActive("Article is not present in the active release.")
    try:
        desired = CapabilityDenyStore().get_desired(article_public_id)
    except CapabilityStoreUnavailable as exc:
        raise CommentServiceError("Comment safety state is unavailable.") from exc
    if desired and desired["policy_version"] > projection.policy_version:
        raise StalePolicy("Comment policy is still applying.")
    if (
        expected_policy_version is not None
        and int(expected_policy_version) != projection.policy_version
    ):
        raise StalePolicy("Comment policy version changed.")
    comments_mode = (
        desired["comments_mode"]
        if desired and desired["policy_version"] == projection.policy_version
        else projection.comments_mode
    )
    if (
        comments_mode == ArticleCapabilityProjection.CommentsMode.HIDDEN
        and not allow_hidden
    ):
        raise CommentsHidden("Comments are hidden for this article.")
    if write and (
        comments_mode != ArticleCapabilityProjection.CommentsMode.OPEN
        or not settings.READER_COMMENTS_WRITE_ENABLED
    ):
        raise CommentsClosed("Comments are closed for this article.")
    return projection


def _serialize_comment(comment, *, viewer_reader_id=None):
    withdrawn = comment.state == Comment.State.WITHDRAWN
    pending = comment.state == Comment.State.PENDING
    return {
        "id": str(comment.public_id),
        "parent_id": str(comment.parent.public_id) if comment.parent_id else None,
        "author": {
            "id": str(comment.reader.public_id),
            "display_name": comment.reader.display_name,
        },
        "body": None if withdrawn else comment.body_plaintext,
        "withdrawn": withdrawn,
        "state": comment.state,
        "version": comment.version,
        "created_at": comment.created_at.isoformat(),
        "updated_at": comment.updated_at.isoformat(),
        "owned_by_viewer": bool(
            viewer_reader_id and comment.reader_id == viewer_reader_id
        ),
        "pending_for_viewer": bool(
            pending and viewer_reader_id and comment.reader_id == viewer_reader_id
        ),
    }


def _visible_filter(viewer_reader_id):
    public = Q(state__in=(Comment.State.PUBLISHED, Comment.State.WITHDRAWN))
    if viewer_reader_id:
        return public | Q(reader_id=viewer_reader_id, state=Comment.State.PENDING)
    return public


def _encode_cursor(comment):
    raw = json.dumps(
        {"created_at": comment.created_at.isoformat(), "id": str(comment.public_id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value):
    if not value:
        return None
    try:
        padded = str(value) + "=" * (-len(str(value)) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        created_at = datetime.fromisoformat(payload["created_at"])
        if timezone.is_naive(created_at):
            raise ValueError
        return created_at, UUID(payload["id"])
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise ValidationError("Invalid comment cursor.") from exc


def _query_comment_page(article_public_id, *, viewer_reader_id, cursor, limit):
    queryset = (
        Comment.objects.filter(
            article_public_id=article_public_id,
            parent__isnull=True,
        )
        .filter(_visible_filter(viewer_reader_id))
        .select_related("reader")
        .order_by("created_at", "public_id")
    )
    decoded = _decode_cursor(cursor)
    if decoded:
        created_at, public_id = decoded
        queryset = queryset.filter(
            Q(created_at__gt=created_at)
            | Q(created_at=created_at, public_id__gt=public_id)
        )
    roots = list(queryset[: limit + 1])
    has_more = len(roots) > limit
    roots = roots[:limit]
    replies = (
        Comment.objects.filter(parent_id__in=[root.pk for root in roots])
        .filter(_visible_filter(viewer_reader_id))
        .select_related("reader", "parent")
        .order_by("created_at", "public_id")
    )
    replies_by_parent = {}
    for reply in replies:
        replies_by_parent.setdefault(reply.parent_id, []).append(
            _serialize_comment(reply, viewer_reader_id=viewer_reader_id)
        )
    items = []
    for root in roots:
        payload = _serialize_comment(root, viewer_reader_id=viewer_reader_id)
        payload["replies"] = replies_by_parent.get(root.pk, [])
        items.append(payload)
    next_cursor = _encode_cursor(roots[-1]) if has_more and roots else None
    result = {"items": items, "next_cursor": next_cursor}
    result["etag"] = (
        '"'
        + hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        + '"'
    )
    return result


def list_comments(
    *, article_public_id, viewer_reader=None, cursor="", limit=None, cache=None
):
    article_public_id = UUID(str(article_public_id))
    limit = int(limit or settings.READER_COMMENT_PAGE_SIZE)
    if not 1 <= limit <= 50:
        raise ValidationError("Comment page limit must be between 1 and 50.")
    with transaction.atomic(using="interactions"):
        _lock_projection(article_public_id, write=False)
    viewer_reader_id = viewer_reader.pk if viewer_reader else None
    if viewer_reader_id:
        return _query_comment_page(
            article_public_id,
            viewer_reader_id=viewer_reader_id,
            cursor=cursor,
            limit=limit,
        )
    cache = cache or CommentCache()
    try:
        cached = cache.get(article_public_id, cursor, limit)
        if cached is not None:
            return cached
        lock = cache.acquire_rebuild_lock(article_public_id)
        if lock is None:
            cached = cache.wait_for(article_public_id, cursor, limit)
            if cached is not None:
                return cached
    except CommentCacheUnavailable:
        cache = None
        lock = None
    try:
        result = _query_comment_page(
            article_public_id,
            viewer_reader_id=None,
            cursor=cursor,
            limit=limit,
        )
        if cache:
            try:
                cache.set(article_public_id, cursor, limit, result)
            except CommentCacheUnavailable:
                pass
        return result
    finally:
        if cache:
            cache.release_rebuild_lock(lock)


def _public_change(article_public_id):
    try:
        CommentCache().invalidate(article_public_id)
    except CommentCacheUnavailable:
        pass
    try:
        from .tasks import refresh_comment_snapshot

        refresh_comment_snapshot.apply_async(
            args=[str(article_public_id)],
            queue="reader_comments",
            argsrepr="(<redacted>,)",
        )
    except Exception as exc:  # noqa: BLE001 - commit already succeeded
        logger.warning(
            "reader_comment_snapshot_enqueue_failed article_id=%s error_type=%s",
            article_public_id,
            type(exc).__name__,
        )


def create_comment(
    *,
    article_public_id,
    reader,
    body,
    expected_policy_version,
    idempotency_key,
    parent_public_id=None,
    risk_assessor=None,
    rate_limiter=None,
    remote_address="0.0.0.0",
):
    article_public_id = UUID(str(article_public_id))
    normalized = normalize_comment_body(body)
    assessment = assess_comment_risk(normalized, assessor=risk_assessor)
    scope = f"comment.create:{article_public_id}:{parent_public_id or 'root'}"
    payload_hash = _request_hash(
        {
            "article_public_id": str(article_public_id),
            "parent_public_id": str(parent_public_id or ""),
            "body": normalized,
            "expected_policy_version": int(expected_policy_version),
        }
    )
    key_hash = _idempotency_key(idempotency_key)
    with transaction.atomic(using="interactions"):
        locked_reader = ReaderIdentity.objects.select_for_update().get(pk=reader.pk)
        if locked_reader.status != ReaderIdentity.Status.ACTIVE:
            raise ReaderSuspended("Reader access is unavailable.")
        replay = _previous_idempotency(locked_reader, scope, key_hash, payload_hash)
        if replay:
            return replay
        _check_rate_limit(
            locked_reader,
            action="comment",
            article_public_id=article_public_id,
            remote_address=remote_address,
            rate_limiter=rate_limiter,
        )
        projection = _lock_projection(
            article_public_id,
            expected_policy_version,
            write=True,
        )
        body_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if Comment.objects.filter(
            article_public_id=article_public_id,
            reader=locked_reader,
            body_sha256=body_sha256,
            state__in=(Comment.State.PENDING, Comment.State.PUBLISHED),
        ).exists():
            raise InvalidComment("Duplicate comment content is not allowed.")
        parent = None
        if parent_public_id:
            parent = (
                Comment.objects.select_for_update()
                .select_related("reader")
                .filter(public_id=parent_public_id, article_public_id=article_public_id)
                .first()
            )
            if parent is None:
                raise CommentNotFound("Reply parent does not exist.")
            if parent.parent_id is not None:
                raise ReplyDepthExceeded("Replies may only target a root comment.")
            if parent.state != Comment.State.PUBLISHED:
                raise InvalidComment("Replies require a published parent comment.")
        state = Comment.State.PENDING if assessment.pending else Comment.State.PUBLISHED
        now = timezone.now()
        comment = Comment.objects.create(
            article_public_id=article_public_id,
            journal_id=projection.journal_id,
            reader=locked_reader,
            parent=parent,
            root=parent,
            body_plaintext=normalized,
            body_sha256=body_sha256,
            state=state,
            risk_score=assessment.score,
            risk_labels=list(assessment.labels),
            published_at=now if state == Comment.State.PUBLISHED else None,
        )
        CommentModerationEvent.objects.create(
            comment=comment,
            from_state="",
            to_state=state,
            action="created",
            actor_type=CommentModerationEvent.ActorType.READER,
            actor_id=str(locked_reader.public_id),
        )
        InteractionOutbox.objects.create(
            event_type="reader.comment.created",
            aggregate_type="comment",
            aggregate_id=str(comment.public_id),
            aggregate_version=comment.version,
            payload={
                "comment_id": str(comment.public_id),
                "article_public_id": str(article_public_id),
                "journal_id": projection.journal_id,
                "state": state,
            },
        )
        response_body = _serialize_comment(comment, viewer_reader_id=locked_reader.pk)
        response_body["replies"] = []
        result = CommentWriteResult(
            202 if state == Comment.State.PENDING else 201,
            response_body,
        )
        _record_idempotency(locked_reader, scope, key_hash, payload_hash, result)
        if state == Comment.State.PUBLISHED:
            transaction.on_commit(lambda: _public_change(article_public_id))
        return result


def withdraw_comment(
    *, article_public_id, comment_public_id, reader, expected_version, idempotency_key
):
    article_public_id = UUID(str(article_public_id))
    scope = f"comment.withdraw:{comment_public_id}"
    payload_hash = _request_hash(
        {
            "comment_public_id": str(comment_public_id),
            "expected_version": int(expected_version),
        }
    )
    key_hash = _idempotency_key(idempotency_key)
    with transaction.atomic(using="interactions"):
        locked_reader = ReaderIdentity.objects.select_for_update().get(pk=reader.pk)
        if locked_reader.status != ReaderIdentity.Status.ACTIVE:
            raise ReaderSuspended("Reader access is unavailable.")
        replay = _previous_idempotency(locked_reader, scope, key_hash, payload_hash)
        if replay:
            return replay
        _lock_projection(article_public_id, write=False, allow_hidden=True)
        comment = (
            Comment.objects.select_for_update()
            .select_related("reader", "parent")
            .filter(public_id=comment_public_id, article_public_id=article_public_id)
            .first()
        )
        if comment is None:
            raise CommentNotFound("Comment does not exist.")
        if comment.reader_id != locked_reader.pk:
            raise NotCommentOwner("Only the comment author may withdraw it.")
        if comment.state == Comment.State.WITHDRAWN:
            body = _serialize_comment(comment, viewer_reader_id=locked_reader.pk)
            result = CommentWriteResult(200, body)
            _record_idempotency(locked_reader, scope, key_hash, payload_hash, result)
            return result
        if comment.version != int(expected_version):
            raise StalePolicy("Comment version changed.")
        if comment.state not in (Comment.State.PUBLISHED, Comment.State.HIDDEN):
            raise InvalidComment("Comment cannot be withdrawn from its current state.")
        previous_state = comment.state
        comment.state = Comment.State.WITHDRAWN
        comment.version += 1
        comment.save(update_fields=("state", "version", "updated_at"))
        CommentModerationEvent.objects.create(
            comment=comment,
            from_state=previous_state,
            to_state=Comment.State.WITHDRAWN,
            action="author_withdrawal",
            actor_type=CommentModerationEvent.ActorType.READER,
            actor_id=str(locked_reader.public_id),
        )
        InteractionOutbox.objects.create(
            event_type="reader.comment.withdrawn",
            aggregate_type="comment",
            aggregate_id=str(comment.public_id),
            aggregate_version=comment.version,
            payload={
                "comment_id": str(comment.public_id),
                "article_public_id": str(article_public_id),
            },
        )
        body = _serialize_comment(comment, viewer_reader_id=locked_reader.pk)
        result = CommentWriteResult(200, body)
        _record_idempotency(locked_reader, scope, key_hash, payload_hash, result)
        transaction.on_commit(lambda: _public_change(article_public_id))
        return result


def report_comment(
    *,
    article_public_id,
    comment_public_id,
    reader,
    reason,
    details,
    idempotency_key,
    rate_limiter=None,
    remote_address="0.0.0.0",
):
    article_public_id = UUID(str(article_public_id))
    reason = str(reason or "")
    details = unicodedata.normalize("NFC", str(details or "")).strip()
    if reason not in CommentReport.Reason.values or len(details) > 1000:
        raise ValidationError("Invalid comment report.")
    scope = f"comment.report:{comment_public_id}"
    payload_hash = _request_hash(
        {
            "comment_public_id": str(comment_public_id),
            "reason": reason,
            "details": details,
        }
    )
    key_hash = _idempotency_key(idempotency_key)
    with transaction.atomic(using="interactions"):
        locked_reader = ReaderIdentity.objects.select_for_update().get(pk=reader.pk)
        if locked_reader.status != ReaderIdentity.Status.ACTIVE:
            raise ReaderSuspended("Reader access is unavailable.")
        replay = _previous_idempotency(locked_reader, scope, key_hash, payload_hash)
        if replay:
            return replay
        _check_rate_limit(
            locked_reader,
            action="report",
            article_public_id=article_public_id,
            remote_address=remote_address,
            rate_limiter=rate_limiter,
        )
        projection = _lock_projection(article_public_id, write=False)
        comment = (
            Comment.objects.select_for_update()
            .filter(
                public_id=comment_public_id,
                article_public_id=article_public_id,
                state=Comment.State.PUBLISHED,
            )
            .first()
        )
        if comment is None:
            raise CommentNotFound("Published comment does not exist.")
        if CommentReport.objects.filter(
            comment=comment,
            reporter=locked_reader,
            status=CommentReport.Status.OPEN,
        ).exists():
            raise AlreadyReported("Comment has already been reported.")
        try:
            with transaction.atomic(using="interactions"):
                report = CommentReport.objects.create(
                    comment=comment,
                    reporter=locked_reader,
                    reason=reason,
                    details=details,
                )
        except IntegrityError as exc:
            raise AlreadyReported("Comment has already been reported.") from exc
        InteractionOutbox.objects.create(
            event_type="reader.comment.reported",
            aggregate_type="comment_report",
            aggregate_id=str(report.public_id),
            aggregate_version=1,
            payload={
                "report_id": str(report.public_id),
                "comment_id": str(comment.public_id),
                "article_public_id": str(article_public_id),
                "journal_id": projection.journal_id,
                "reason": reason,
            },
        )
        body = {
            "id": str(report.public_id),
            "comment_id": str(comment.public_id),
            "status": report.status,
        }
        result = CommentWriteResult(201, body)
        _record_idempotency(locked_reader, scope, key_hash, payload_hash, result)
        return result
