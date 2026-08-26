"""Readiness checks enabled only when the reader data plane is active."""

from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from ai_author_forum.static_publish.models import StaticManifest

from .crypto import EmailProtector
from .rate_limits import RateLimitUnavailable, RedisAtomicRateLimiter


def reader_dependency_health():
    checks = {}
    try:
        with connections["interactions"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - readiness returns only an error class
        checks["interactions_database"] = (
            False,
            f"interactions database unavailable: {type(exc).__name__}",
        )
    else:
        checks["interactions_database"] = (True, "interactions database available")
        try:
            connection = connections["interactions"]
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except Exception as exc:  # noqa: BLE001 - readiness returns error class only
            checks["interactions_migrations"] = (
                False,
                f"interaction migration state unavailable: {type(exc).__name__}",
            )
        else:
            checks["interactions_migrations"] = (
                not pending,
                (
                    "interaction migrations current"
                    if not pending
                    else "interaction migrations pending"
                ),
            )

        if connections["interactions"].vendor == "postgresql":
            try:
                with connections["interactions"].cursor() as cursor:
                    cursor.execute(
                        "SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user"
                    )
                    role_limit = int(cursor.fetchone()[0])
                configured_limit = settings.READER_INTERACTIONS_DB_CONNECTION_LIMIT
                valid_limit = 0 < role_limit <= configured_limit
            except Exception as exc:  # noqa: BLE001 - no role name is exposed
                checks["interactions_connection_limit"] = (
                    False,
                    f"interaction connection limit unavailable: {type(exc).__name__}",
                )
            else:
                checks["interactions_connection_limit"] = (
                    valid_limit,
                    (
                        "interaction database role connection limit is enforced"
                        if valid_limit
                        else "interaction database role connection limit is not enforced"
                    ),
                )

    try:
        EmailProtector.from_settings()
    except Exception as exc:  # noqa: BLE001 - no secret values enter the message
        checks["reader_crypto"] = (
            False,
            f"reader cryptography unavailable: {type(exc).__name__}",
        )
    else:
        checks["reader_crypto"] = (True, "reader cryptography configured")

    try:
        RedisAtomicRateLimiter().ping()
    except RateLimitUnavailable as exc:
        checks["reader_rate_limit"] = (
            False,
            f"reader rate limiting unavailable: {type(exc).__name__}",
        )
    else:
        checks["reader_rate_limit"] = (True, "reader rate limiting available")

    try:
        active_release = (
            StaticManifest.objects.filter(is_active=True)
            .values_list("version", flat=True)
            .get()
        )
        from .models import ArticleCapabilityProjection

        projected_releases = set(
            ArticleCapabilityProjection.objects.exclude(active_release="")
            .values_list("active_release", flat=True)
            .distinct()
        )
        projection_matches = not projected_releases or projected_releases == {
            active_release
        }
    except Exception as exc:  # noqa: BLE001 - readiness returns error class only
        checks["reader_projection_release"] = (
            False,
            f"reader projection state unavailable: {type(exc).__name__}",
        )
    else:
        checks["reader_projection_release"] = (
            projection_matches,
            (
                "reader projections match the active release"
                if projection_matches
                else "reader projections do not match the active release"
            ),
        )

    if settings.READER_PDF_GRANTS_ENABLED:
        if settings.READER_PRIVATE_STORAGE_BACKEND == "filesystem":
            root = Path(settings.READER_PRIVATE_STORAGE_ROOT)
            try:
                storage_ready = root.is_dir() and root.stat().st_mode != 0
            except OSError:
                storage_ready = False
        else:
            storage_ready = bool(
                settings.READER_S3_BUCKET
                and settings.READER_S3_REGION
                and settings.READER_S3_ACCESS_KEY_ID
                and settings.READER_S3_SECRET_ACCESS_KEY
            )
        checks["reader_private_storage"] = (
            storage_ready,
            (
                "reader private storage is configured"
                if storage_ready
                else "reader private storage is unavailable"
            ),
        )
    return checks
