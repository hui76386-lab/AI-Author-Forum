"""Versioned public comment snapshot metadata and immutable filesystem objects."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import Comment, CommentSnapshot


def _snapshot_payload(article_public_id):
    comments = (
        Comment.objects.filter(
            article_public_id=article_public_id,
            state__in=(Comment.State.PUBLISHED, Comment.State.WITHDRAWN),
        )
        .select_related("reader")
        .prefetch_related("replies__reader")
        .order_by("created_at", "public_id")
    )
    items = []
    latest_content_change = None
    for comment in comments:
        if comment.parent_id:
            continue
        latest_content_change = max(
            filter(None, (latest_content_change, comment.updated_at))
        )
        item = {
            "id": str(comment.public_id),
            "author": {
                "id": str(comment.reader.public_id),
                "display_name": comment.reader.display_name,
            },
            "body": (
                None
                if comment.state == Comment.State.WITHDRAWN
                else comment.body_plaintext
            ),
            "withdrawn": comment.state == Comment.State.WITHDRAWN,
            "created_at": comment.created_at.isoformat(),
            "replies": [],
        }
        for reply in comment.replies.all():
            if reply.state not in (Comment.State.PUBLISHED, Comment.State.WITHDRAWN):
                continue
            latest_content_change = max(
                filter(None, (latest_content_change, reply.updated_at))
            )
            item["replies"].append(
                {
                    "id": str(reply.public_id),
                    "author": {
                        "id": str(reply.reader.public_id),
                        "display_name": reply.reader.display_name,
                    },
                    "body": (
                        None
                        if reply.state == Comment.State.WITHDRAWN
                        else reply.body_plaintext
                    ),
                    "withdrawn": reply.state == Comment.State.WITHDRAWN,
                    "created_at": reply.created_at.isoformat(),
                }
            )
        items.append(item)
    return {
        "schema_version": 1,
        "article_public_id": str(article_public_id),
        "generated_at": (
            latest_content_change.isoformat() if latest_content_change else None
        ),
        "items": items,
    }


def _write_immutable_object(object_key, raw):
    root = Path(settings.READER_COMMENT_SNAPSHOT_ROOT)
    target = root / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != raw:
            raise RuntimeError("Immutable comment snapshot object changed.")
        return target
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def rebuild_comment_snapshot(article_public_id):
    article_public_id = UUID(str(article_public_id))
    payload = _snapshot_payload(article_public_id)
    fingerprint = dict(payload)
    fingerprint.pop("generated_at", None)
    etag = hashlib.sha256(
        json.dumps(
            fingerprint, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    latest = (
        CommentSnapshot.objects.filter(article_public_id=article_public_id)
        .order_by("-version")
        .first()
    )
    if latest and latest.etag == etag:
        return latest
    for attempt in range(3):
        try:
            with transaction.atomic(using="interactions"):
                latest = (
                    CommentSnapshot.objects.select_for_update()
                    .filter(article_public_id=article_public_id)
                    .order_by("-version")
                    .first()
                )
                if latest and latest.etag == etag:
                    return latest
                version = (latest.version if latest else 0) + 1
                object_key = f"articles/{article_public_id}/comments/v{version}.json"
                _write_immutable_object(object_key, raw)
                return CommentSnapshot.objects.create(
                    article_public_id=article_public_id,
                    version=version,
                    object_key=object_key,
                    etag=etag,
                    comment_count=sum(
                        1 + len(item["replies"]) for item in payload["items"]
                    ),
                )
        except IntegrityError:
            if attempt == 2:
                raise
    raise RuntimeError("Comment snapshot retry exhausted.")


def read_comment_snapshot(article_public_id):
    snapshot = (
        CommentSnapshot.objects.filter(article_public_id=UUID(str(article_public_id)))
        .order_by("-version")
        .first()
    )
    if snapshot is None:
        return None
    target = Path(settings.READER_COMMENT_SNAPSHOT_ROOT) / snapshot.object_key
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    payload["snapshot_version"] = snapshot.version
    payload["etag"] = '"' + snapshot.etag + '"'
    return payload
