import os
from pathlib import Path


def _load_local_env_file():
    """Load ignored local development values before base settings are evaluated."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_env_file()

from .base import *  # noqa: E402

# Local development must remain usable when a deployment-oriented .env points
# Django's cache at a Redis tunnel that is not running. Redis can still be used
# explicitly for cache integration testing by opting in with this flag.
if os.environ.get("USE_REDIS_CACHE_IN_DEV", "").strip().lower() not in {
    "1",
    "true",
    "yes",
    "on",
}:
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ai-author-forum-dev",
    }

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-ai-author-forum-dev-only")

# SECURITY WARNING: define the correct hosts in production!
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

STORAGES["staticfiles"][
    "BACKEND"
] = "django.contrib.staticfiles.storage.StaticFilesStorage"


try:
    from .local import *
except ImportError:
    pass
