import pytest
from django.core.exceptions import ImproperlyConfigured

from ai_author_forum.settings.middleware import (
    validate_production_middleware_environment,
)

VALID_REMOTE = {
    "MIDDLEWARE_MODE": "remote",
    "DATABASE_URL": "postgresql://cms:secret@postgres.internal:5432/cms",
    "INTERACTIONS_DATABASE_URL": (
        "postgresql://reader:secret@interactions.internal:5432/interactions"
    ),
    "CACHE_BACKEND": "django.core.cache.backends.redis.RedisCache",
    "CACHE_LOCATION": "rediss://:secret@redis.internal:6380/1",
    "CELERY_BROKER_URL": "rediss://:secret@redis.internal:6380/0",
    "CELERY_RESULT_BACKEND": "rediss://:secret@redis.internal:6380/0",
}


def test_remote_middleware_configuration_is_accepted():
    validate_production_middleware_environment(VALID_REMOTE)


def test_missing_remote_middleware_variable_is_rejected():
    config = VALID_REMOTE.copy()
    config.pop("DATABASE_URL")

    with pytest.raises(ImproperlyConfigured, match="DATABASE_URL"):
        validate_production_middleware_environment(config)


def test_production_rejects_local_service_hosts():
    config = VALID_REMOTE | {
        "DATABASE_URL": "postgresql://cms:secret@database:5432/cms",
    }

    with pytest.raises(ImproperlyConfigured, match="local middleware host"):
        validate_production_middleware_environment(config)


def test_local_overlay_may_use_compose_service_hosts():
    local = {
        "MIDDLEWARE_MODE": "local",
        "DATABASE_URL": "postgresql://cms:secret@database:5432/cms",
        "INTERACTIONS_DATABASE_URL": (
            "postgresql://reader:secret@database:5432/interactions"
        ),
        "CACHE_BACKEND": "django.core.cache.backends.redis.RedisCache",
        "CACHE_LOCATION": "redis://redis:6379/1",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/0",
    }

    validate_production_middleware_environment(local)


def test_production_rejects_non_redis_cache_backend():
    config = VALID_REMOTE | {
        "CACHE_BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }

    with pytest.raises(ImproperlyConfigured, match="CACHE_BACKEND"):
        validate_production_middleware_environment(config)


def test_interactions_database_is_required_and_must_use_postgresql():
    missing = VALID_REMOTE.copy()
    missing.pop("INTERACTIONS_DATABASE_URL")
    with pytest.raises(ImproperlyConfigured, match="INTERACTIONS_DATABASE_URL"):
        validate_production_middleware_environment(missing)

    invalid = VALID_REMOTE | {
        "INTERACTIONS_DATABASE_URL": "sqlite:///interactions.sqlite3"
    }
    with pytest.raises(ImproperlyConfigured, match="INTERACTIONS_DATABASE_URL"):
        validate_production_middleware_environment(invalid)


def test_invalid_url_schemes_are_rejected_without_echoing_secrets():
    config = VALID_REMOTE | {
        "CELERY_BROKER_URL": "amqp://user:super-secret@broker.internal/vhost",
    }

    with pytest.raises(ImproperlyConfigured) as exc_info:
        validate_production_middleware_environment(config)

    assert "super-secret" not in str(exc_info.value)


def test_enabled_reader_requires_distinct_database_user_and_connection_limit():
    enabled = VALID_REMOTE | {
        "READER_INTERACTIONS_ENABLED": "true",
        "READER_INTERACTIONS_DB_CONNECTION_LIMIT": "20",
    }
    validate_production_middleware_environment(enabled)

    shared_user = enabled | {
        "INTERACTIONS_DATABASE_URL": (
            "postgresql://cms:secret@interactions.internal:5432/interactions"
        )
    }
    with pytest.raises(ImproperlyConfigured, match="distinct"):
        validate_production_middleware_environment(shared_user)

    invalid_limit = enabled | {"READER_INTERACTIONS_DB_CONNECTION_LIMIT": "0"}
    with pytest.raises(ImproperlyConfigured, match="must be positive"):
        validate_production_middleware_environment(invalid_limit)
