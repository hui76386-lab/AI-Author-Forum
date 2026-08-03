import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REDIS_BACKEND = "django.core.cache.backends.redis.RedisCache"
LOCMEM_BACKEND = "django.core.cache.backends.locmem.LocMemCache"


def load_dev_cache(*, use_redis: bool) -> tuple[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "CACHE_BACKEND": REDIS_BACKEND,
            "CACHE_LOCATION": "redis://127.0.0.1:16379/2",
        }
    )
    if use_redis:
        environment["USE_REDIS_CACHE_IN_DEV"] = "true"
    else:
        environment.pop("USE_REDIS_CACHE_IN_DEV", None)

    command = (
        "import ai_author_forum.settings.dev as settings; "
        "cache = settings.CACHES['default']; "
        "print(cache['BACKEND']); print(cache['LOCATION'])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    backend, location = completed.stdout.strip().splitlines()
    return backend, location


def test_dev_cache_defaults_to_locmem_when_redis_is_configured():
    assert load_dev_cache(use_redis=False) == (
        LOCMEM_BACKEND,
        "ai-author-forum-dev",
    )


def test_dev_cache_can_explicitly_opt_into_redis():
    assert load_dev_cache(use_redis=True) == (
        REDIS_BACKEND,
        "redis://127.0.0.1:16379/2",
    )
