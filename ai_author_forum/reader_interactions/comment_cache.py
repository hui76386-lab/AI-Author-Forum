"""Short-lived public comment cache with single-flight rebuild locking."""

from __future__ import annotations

import json
import secrets
import time

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class CommentCacheUnavailable(RuntimeError):
    pass


class CommentCache:
    prefix = "{reader-comments}:article:"

    def __init__(self, client=None):
        self.client = client

    def _client(self):
        if self.client is not None:
            return self.client
        if not settings.READER_COMMENT_CACHE_REDIS_URL:
            raise CommentCacheUnavailable("Comment cache Redis is not configured.")
        self.client = Redis.from_url(
            settings.READER_COMMENT_CACHE_REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        return self.client

    def key(self, article_public_id, cursor, limit):
        return f"{self.prefix}{article_public_id}:page:{cursor or 'first'}:{limit}"

    def get(self, article_public_id, cursor, limit):
        try:
            raw = self._client().get(self.key(article_public_id, cursor, limit))
            return json.loads(raw) if raw else None
        except (
            RedisError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise CommentCacheUnavailable("Comment cache could not be read.") from exc

    def set(self, article_public_id, cursor, limit, value):
        try:
            self._client().setex(
                self.key(article_public_id, cursor, limit),
                settings.READER_COMMENT_CACHE_SECONDS,
                json.dumps(value, sort_keys=True, separators=(",", ":")),
            )
        except (RedisError, OSError, TypeError, ValueError) as exc:
            raise CommentCacheUnavailable(
                "Comment cache could not be written."
            ) from exc

    def invalidate(self, article_public_id):
        try:
            client = self._client()
            cursor = 0
            pattern = f"{self.prefix}{article_public_id}:page:*"
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    client.delete(*keys)
                if int(cursor) == 0:
                    break
        except (RedisError, OSError, TypeError, ValueError) as exc:
            raise CommentCacheUnavailable(
                "Comment cache could not be invalidated."
            ) from exc

    def acquire_rebuild_lock(self, article_public_id):
        token = secrets.token_hex(16)
        key = f"{self.prefix}{article_public_id}:rebuild-lock"
        try:
            acquired = self._client().set(key, token, nx=True, ex=5)
        except (RedisError, OSError) as exc:
            raise CommentCacheUnavailable("Comment cache lock is unavailable.") from exc
        return (key, token) if acquired else None

    def wait_for(self, article_public_id, cursor, limit, *, seconds=0.25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            cached = self.get(article_public_id, cursor, limit)
            if cached is not None:
                return cached
            time.sleep(0.025)
        return None

    def release_rebuild_lock(self, lock):
        if lock is None:
            return
        key, token = lock
        try:
            self._client().eval(_UNLOCK, 1, key, token)
        except (RedisError, OSError):
            return
