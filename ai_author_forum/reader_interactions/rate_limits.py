"""Atomic, multi-dimension Redis rate limiting for sensitive reader actions."""

from __future__ import annotations

import time
from dataclasses import dataclass

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

_MULTI_LIMIT_SCRIPT = """
local window = tonumber(ARGV[#ARGV])
local retry_after = 0
for index, key in ipairs(KEYS) do
    local current = tonumber(redis.call('GET', key) or '0')
    local limit = tonumber(ARGV[index])
    if current >= limit then
        local ttl = redis.call('TTL', key)
        if ttl < 1 then ttl = window end
        if ttl > retry_after then retry_after = ttl end
    end
end
if retry_after > 0 then return {0, retry_after} end
for _, key in ipairs(KEYS) do
    local value = redis.call('INCR', key)
    if value == 1 then redis.call('EXPIRE', key, window) end
end
return {1, 0}
"""

_WINDOWED_LIMIT_SCRIPT = """
local retry_after = 0
for index, key in ipairs(KEYS) do
    local arg_index = (index - 1) * 2 + 1
    local limit = tonumber(ARGV[arg_index])
    local window = tonumber(ARGV[arg_index + 1])
    local current = tonumber(redis.call('GET', key) or '0')
    if current >= limit then
        local ttl = redis.call('TTL', key)
        if ttl < 1 then ttl = window end
        if ttl > retry_after then retry_after = ttl end
    end
end
if retry_after > 0 then return {0, retry_after} end
for index, key in ipairs(KEYS) do
    local arg_index = (index - 1) * 2 + 1
    local window = tonumber(ARGV[arg_index + 1])
    local value = redis.call('INCR', key)
    if value == 1 then redis.call('EXPIRE', key, window) end
end
return {1, 0}
"""


class RateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


class RedisAtomicRateLimiter:
    def __init__(self, redis_url=None, *, namespace="reader-api"):
        self.redis_url = redis_url or settings.READER_RATE_LIMIT_REDIS_URL
        self.namespace = namespace
        self._client = None

    def _get_client(self):
        if not self.redis_url.startswith(("redis://", "rediss://")):
            raise RateLimitUnavailable("Atomic rate limiting requires Redis.")
        if self._client is None:
            self._client = Redis.from_url(
                self.redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
                decode_responses=True,
            )
        return self._client

    def check(self, dimensions, *, window_seconds):
        bucket = int(time.time() // max(1, window_seconds))
        keys = [
            f"{self.namespace}:{{reader-rate-limit}}:{name}:{value}:{bucket}"
            for name, value, _limit in dimensions
        ]
        limits = [int(limit) for _name, _value, limit in dimensions]
        try:
            allowed, retry_after = self._get_client().eval(
                _MULTI_LIMIT_SCRIPT,
                len(keys),
                *keys,
                *limits,
                int(window_seconds),
            )
        except (RedisError, OSError, ValueError) as exc:
            raise RateLimitUnavailable("Atomic rate limiting is unavailable.") from exc
        return RateLimitDecision(bool(allowed), int(retry_after))

    def check_windowed(self, dimensions):
        """Check windows with different TTLs in one atomic Lua evaluation."""
        if not dimensions:
            return RateLimitDecision(True)
        if not self.redis_url.startswith(("redis://", "rediss://")):
            raise RateLimitUnavailable("Atomic rate limiting requires Redis.")
        keys = []
        args = []
        now = int(time.time())
        for name, value, limit, window_seconds in dimensions:
            window_seconds = max(1, int(window_seconds))
            bucket = now // window_seconds
            keys.append(
                f"{self.namespace}:{{reader-rate-limit}}:{name}:{value}:{bucket}"
            )
            args.extend((int(limit), window_seconds))
        try:
            allowed, retry_after = self._get_client().eval(
                _WINDOWED_LIMIT_SCRIPT,
                len(keys),
                *keys,
                *args,
            )
        except (RedisError, OSError, ValueError) as exc:
            raise RateLimitUnavailable("Atomic rate limiting is unavailable.") from exc
        return RateLimitDecision(bool(allowed), int(retry_after))

    def ping(self):
        try:
            return bool(self._get_client().ping())
        except (RedisError, OSError, ValueError) as exc:
            raise RateLimitUnavailable("Atomic rate limiting is unavailable.") from exc
