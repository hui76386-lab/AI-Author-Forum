"""Validation for production database, cache, and task-queue settings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

REQUIRED_MIDDLEWARE_ENV = (
    "DATABASE_URL",
    "INTERACTIONS_DATABASE_URL",
    "CACHE_BACKEND",
    "CACHE_LOCATION",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
)
REDIS_URL_ENV = (
    "CACHE_LOCATION",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
)
_LOCAL_MIDDLEWARE_HOSTNAMES = frozenset(
    {"localhost", "127.0.0.1", "::1", "database", "redis"}
)


def _parse_url(name: str, value: str, schemes: set[str]):
    parsed = urlparse(value)
    if parsed.scheme not in schemes or not parsed.hostname:
        expected = ", ".join(sorted(schemes))
        raise ImproperlyConfigured(
            f"{name} must be a URL using one of: {expected}; "
            "the value is intentionally not included in this error"
        )
    return parsed


def validate_production_middleware_environment(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail fast when production would silently use local middleware.

    ``MIDDLEWARE_MODE=remote`` is the default and rejects Docker service names
    and loopback hosts. The local Compose overlay sets ``MIDDLEWARE_MODE=local``
    explicitly so local acceptance remains available without changing the
    production default.
    """

    env = os.environ if environ is None else environ
    mode = env.get("MIDDLEWARE_MODE", "remote").strip().lower()
    if mode not in {"remote", "local"}:
        raise ImproperlyConfigured("MIDDLEWARE_MODE must be either 'remote' or 'local'")

    missing = [name for name in REQUIRED_MIDDLEWARE_ENV if not env.get(name)]
    if missing:
        raise ImproperlyConfigured(
            "Production middleware variables are missing: " + ", ".join(missing)
        )

    cache_backend = env["CACHE_BACKEND"].strip()
    if cache_backend != "django.core.cache.backends.redis.RedisCache":
        raise ImproperlyConfigured(
            "Production CACHE_BACKEND must be "
            "django.core.cache.backends.redis.RedisCache"
        )

    database_url = _parse_url(
        "DATABASE_URL", env["DATABASE_URL"], {"postgres", "postgresql"}
    )
    interactions_database_url = _parse_url(
        "INTERACTIONS_DATABASE_URL",
        env["INTERACTIONS_DATABASE_URL"],
        {"postgres", "postgresql"},
    )
    redis_urls = {
        name: _parse_url(name, env[name], {"redis", "rediss"}) for name in REDIS_URL_ENV
    }

    if mode == "remote":
        urls = {
            "DATABASE_URL": database_url,
            "INTERACTIONS_DATABASE_URL": interactions_database_url,
            **redis_urls,
        }
        for name, parsed in urls.items():
            if (
                parsed.hostname
                and parsed.hostname.lower() in _LOCAL_MIDDLEWARE_HOSTNAMES
            ):
                raise ImproperlyConfigured(
                    f"{name} points to a local middleware host ({parsed.hostname}); "
                    "use MIDDLEWARE_MODE=local only with the local Compose overlay"
                )

    reader_enabled = env.get("READER_INTERACTIONS_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if reader_enabled:
        if not database_url.username or not interactions_database_url.username:
            raise ImproperlyConfigured(
                "Production database URLs must include explicit least-privilege users"
            )
        if database_url.username == interactions_database_url.username:
            raise ImproperlyConfigured(
                "Reader interactions require a database user distinct from the control plane"
            )
        try:
            connection_limit = int(
                env.get("READER_INTERACTIONS_DB_CONNECTION_LIMIT", "0")
            )
        except ValueError as exc:
            raise ImproperlyConfigured(
                "READER_INTERACTIONS_DB_CONNECTION_LIMIT must be an integer"
            ) from exc
        if connection_limit < 1:
            raise ImproperlyConfigured(
                "READER_INTERACTIONS_DB_CONNECTION_LIMIT must be positive"
            )
