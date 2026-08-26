import os

from .base import *  # noqa
from .middleware import validate_production_middleware_environment
from .reader_security import validate_reader_security_environment

DEBUG = False

# Full static releases in deployed environments must pass managed-content readiness checks.
STATIC_PUBLISH_ENFORCE_CONTENT_READINESS = os.environ.get(
    "STATIC_PUBLISH_ENFORCE_CONTENT_READINESS", "true"
).strip().lower() in {"1", "true", "yes", "on"}

if not os.environ.get("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY must be set in production")
validate_production_middleware_environment()
validate_reader_security_environment()


# Security configuration

# Ensure that the session cookie is only sent by browsers under an HTTPS connection.
# https://docs.djangoproject.com/en/stable/ref/settings/#session-cookie-secure
SESSION_COOKIE_SECURE = (
    os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
)

# Ensure that the CSRF cookie is only sent by browsers under an HTTPS connection.
# https://docs.djangoproject.com/en/stable/ref/settings/#csrf-cookie-secure
CSRF_COOKIE_SECURE = os.environ.get("CSRF_COOKIE_SECURE", "true").lower() == "true"
READER_SESSION_COOKIE_SECURE = True
READER_DEVICE_FLOW_COOKIE_SECURE = True
READER_TRUST_PROXY_CLIENT_IP = os.environ.get(
    "READER_TRUST_PROXY_CLIENT_IP", "true"
).lower() in {"1", "true", "yes", "on"}

# Allow the redirect importer to work in load-balanced / cloud environments.
# https://docs.wagtail.io/en/v2.13/reference/settings.html#redirects
WAGTAIL_REDIRECTS_FILE_STORAGE = "cache"

# Force HTTPS redirect (enabled by default!)
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "true").lower() == "true"
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = (
    os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "false").lower() == "true"
)
SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "false").lower() == "true"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer-when-downgrade"
