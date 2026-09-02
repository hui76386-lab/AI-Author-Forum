"""Fail-fast validation for reader identity secrets in production."""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

_REQUIRED_SECRETS = (
    "READER_EMAIL_LOOKUP_KEY",
    "READER_EMAIL_ENCRYPTION_KEYS",
    "READER_TOKEN_PEPPER",
    "READER_PUBLIC_BASE_URL",
    "READER_EMAIL_FROM",
    "EMAIL_BACKEND",
    "READER_INTERNAL_SERVICE_TOKEN",
)

_NON_DELIVERY_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
}


def _enabled(env, name):
    return env.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _validate_fernet_keys(raw):
    versions = set()
    for entry in raw.split(","):
        try:
            version_text, encoded_key = entry.strip().split(":", 1)
            version = int(version_text)
            decoded = base64.urlsafe_b64decode(encoded_key.encode())
        except (TypeError, ValueError, base64.binascii.Error) as exc:
            raise ImproperlyConfigured(
                "READER_EMAIL_ENCRYPTION_KEYS must contain versioned Fernet keys"
            ) from exc
        if version < 1 or version in versions or len(decoded) != 32:
            raise ImproperlyConfigured(
                "READER_EMAIL_ENCRYPTION_KEYS contains an invalid version or key"
            )
        versions.add(version)
    if not versions:
        raise ImproperlyConfigured("READER_EMAIL_ENCRYPTION_KEYS cannot be empty")


def validate_reader_security_environment(
    environ: Mapping[str, str] | None = None,
) -> None:
    env = os.environ if environ is None else environ
    interactions_enabled = _enabled(env, "READER_INTERACTIONS_ENABLED")
    dependent_flags = (
        "READER_EMAIL_VERIFICATION_ENABLED",
        "READER_COMMENTS_WRITE_ENABLED",
        "READER_PDF_GRANTS_ENABLED",
        "READER_SHARE_UI_ENABLED",
    )
    enabled_dependents = [name for name in dependent_flags if _enabled(env, name)]
    if not interactions_enabled and not enabled_dependents:
        return
    if enabled_dependents and not interactions_enabled:
        raise ImproperlyConfigured(
            ", ".join(enabled_dependents)
            + " require READER_INTERACTIONS_ENABLED"
        )

    missing = [name for name in _REQUIRED_SECRETS if not env.get(name)]
    if missing:
        raise ImproperlyConfigured(
            "Reader security variables are missing: " + ", ".join(missing)
        )
    for name in ("READER_EMAIL_LOOKUP_KEY", "READER_TOKEN_PEPPER"):
        if len(env[name]) < 32:
            raise ImproperlyConfigured(f"{name} must contain at least 32 characters")
    if len(env["READER_INTERNAL_SERVICE_TOKEN"]) < 32:
        raise ImproperlyConfigured(
            "READER_INTERNAL_SERVICE_TOKEN must contain at least 32 characters"
        )
    _validate_fernet_keys(env["READER_EMAIL_ENCRYPTION_KEYS"])

    public_url = urlparse(env["READER_PUBLIC_BASE_URL"])
    if public_url.scheme != "https" or not public_url.hostname:
        raise ImproperlyConfigured(
            "READER_PUBLIC_BASE_URL must be an absolute HTTPS URL"
        )
    if "@" not in env["READER_EMAIL_FROM"]:
        raise ImproperlyConfigured("READER_EMAIL_FROM must be an email address")
    if env.get("READER_SESSION_COOKIE_SECURE", "true").lower() != "true":
        raise ImproperlyConfigured(
            "READER_SESSION_COOKIE_SECURE must be true in production"
        )
    if env.get("READER_DEVICE_FLOW_COOKIE_SECURE", "true").lower() != "true":
        raise ImproperlyConfigured(
            "READER_DEVICE_FLOW_COOKIE_SECURE must be true in production"
        )
    email_backend = env["EMAIL_BACKEND"].strip()
    if email_backend in _NON_DELIVERY_EMAIL_BACKENDS:
        raise ImproperlyConfigured(
            "EMAIL_BACKEND must use a production delivery adapter when reader "
            "verification is enabled"
        )
    if email_backend == "django.core.mail.backends.smtp.EmailBackend" and not env.get(
        "EMAIL_HOST"
    ):
        raise ImproperlyConfigured("EMAIL_HOST is required for the SMTP email backend")
    if _enabled(env, "EMAIL_USE_TLS") and _enabled(env, "EMAIL_USE_SSL"):
        raise ImproperlyConfigured(
            "EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive"
        )

    for redis_name in ("READER_RATE_LIMIT_REDIS_URL", "READER_CAPABILITY_REDIS_URL"):
        redis_url = urlparse(env.get(redis_name) or env.get("CACHE_LOCATION", ""))
        if redis_url.scheme not in {"redis", "rediss"} or not redis_url.hostname:
            raise ImproperlyConfigured(
                f"{redis_name} or CACHE_LOCATION must be a Redis URL"
            )

    if _enabled(env, "READER_PDF_GRANTS_ENABLED"):
        try:
            grant_ttl = int(env.get("READER_DOWNLOAD_GRANT_TTL_SECONDS", "300"))
        except ValueError as exc:
            raise ImproperlyConfigured(
                "READER_DOWNLOAD_GRANT_TTL_SECONDS must be an integer"
            ) from exc
        if not 1 <= grant_ttl <= 300:
            raise ImproperlyConfigured(
                "READER_DOWNLOAD_GRANT_TTL_SECONDS must be between 1 and 300"
            )
        backend = env.get("READER_PRIVATE_STORAGE_BACKEND", "").strip().lower()
        if backend == "filesystem":
            root = env.get("READER_PRIVATE_STORAGE_ROOT", "")
            if not root.startswith("/") or "/media" in root.rstrip("/").lower():
                raise ImproperlyConfigured(
                    "READER_PRIVATE_STORAGE_ROOT must be an absolute private path"
                )
        elif backend == "s3":
            if not env.get("READER_S3_BUCKET"):
                raise ImproperlyConfigured(
                    "READER_S3_BUCKET is required for private S3 storage"
                )
        else:
            raise ImproperlyConfigured(
                "READER_PRIVATE_STORAGE_BACKEND must be filesystem or s3"
            )
