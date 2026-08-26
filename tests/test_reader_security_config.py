from base64 import urlsafe_b64encode

import pytest
from django.core.exceptions import ImproperlyConfigured

from ai_author_forum.settings.reader_security import (
    validate_reader_security_environment,
)


def enabled_environment():
    return {
        "READER_INTERACTIONS_ENABLED": "true",
        "READER_EMAIL_VERIFICATION_ENABLED": "true",
        "READER_EMAIL_LOOKUP_KEY": "l" * 32,
        "READER_EMAIL_ENCRYPTION_KEYS": f"7:{urlsafe_b64encode(b'k' * 32).decode()}",
        "READER_TOKEN_PEPPER": "p" * 32,
        "READER_INTERNAL_SERVICE_TOKEN": "s" * 32,
        "READER_PUBLIC_BASE_URL": "https://reader.example.org",
        "READER_EMAIL_FROM": "reader@example.org",
        "READER_SESSION_COOKIE_SECURE": "true",
        "CACHE_LOCATION": "rediss://redis.example.org:6380/1",
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": "smtp.example.org",
    }


def test_reader_security_secrets_are_not_required_while_flags_are_closed():
    validate_reader_security_environment({})


@pytest.mark.parametrize(
    "missing",
    [
        "READER_EMAIL_LOOKUP_KEY",
        "READER_EMAIL_ENCRYPTION_KEYS",
        "READER_TOKEN_PEPPER",
        "READER_PUBLIC_BASE_URL",
        "READER_EMAIL_FROM",
        "READER_INTERNAL_SERVICE_TOKEN",
    ],
)
def test_enabled_production_reader_security_fails_when_secret_is_missing(missing):
    environment = enabled_environment()
    environment.pop(missing)
    with pytest.raises(ImproperlyConfigured, match=missing):
        validate_reader_security_environment(environment)


def test_enabled_production_reader_security_requires_https_and_secure_cookie():
    environment = enabled_environment() | {
        "READER_PUBLIC_BASE_URL": "http://reader.example.org"
    }
    with pytest.raises(ImproperlyConfigured, match="HTTPS"):
        validate_reader_security_environment(environment)

    environment = enabled_environment() | {"READER_SESSION_COOKIE_SECURE": "false"}
    with pytest.raises(ImproperlyConfigured, match="COOKIE_SECURE"):
        validate_reader_security_environment(environment)


def test_enabled_production_reader_security_accepts_versioned_fernet_keys():
    validate_reader_security_environment(enabled_environment())


def test_enabled_production_reader_security_rejects_non_delivery_email_backend():
    environment = enabled_environment() | {
        "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"
    }
    with pytest.raises(ImproperlyConfigured, match="production delivery adapter"):
        validate_reader_security_environment(environment)


def test_enabled_production_reader_security_requires_atomic_redis():
    environment = enabled_environment() | {"CACHE_LOCATION": "memory://local"}
    with pytest.raises(ImproperlyConfigured, match="Redis URL"):
        validate_reader_security_environment(environment)


def test_pdf_flag_requires_parent_flag_private_storage_and_five_minute_ttl():
    environment = enabled_environment() | {
        "READER_PDF_GRANTS_ENABLED": "true",
        "READER_PRIVATE_STORAGE_BACKEND": "filesystem",
        "READER_PRIVATE_STORAGE_ROOT": "/data/protected-pdfs",
    }
    validate_reader_security_environment(environment)

    without_parent = environment | {"READER_INTERACTIONS_ENABLED": "false"}
    with pytest.raises(ImproperlyConfigured, match="READER_INTERACTIONS_ENABLED"):
        validate_reader_security_environment(without_parent)

    public_root = environment | {"READER_PRIVATE_STORAGE_ROOT": "/data/media/pdfs"}
    with pytest.raises(ImproperlyConfigured, match="absolute private path"):
        validate_reader_security_environment(public_root)

    long_ttl = environment | {"READER_DOWNLOAD_GRANT_TTL_SECONDS": "301"}
    with pytest.raises(ImproperlyConfigured, match="between 1 and 300"):
        validate_reader_security_environment(long_ttl)


def test_pdf_s3_backend_requires_private_bucket_name():
    environment = enabled_environment() | {
        "READER_PDF_GRANTS_ENABLED": "true",
        "READER_PRIVATE_STORAGE_BACKEND": "s3",
    }
    with pytest.raises(ImproperlyConfigured, match="READER_S3_BUCKET"):
        validate_reader_security_environment(environment)
    validate_reader_security_environment(
        environment | {"READER_S3_BUCKET": "reader-private-pdfs"}
    )
