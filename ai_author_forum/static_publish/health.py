import json
from pathlib import Path

from django.conf import settings
from django.db import connection
from kombu import Connection

from .models import StaticManifest


def database_health():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return False, f"database unavailable: {type(exc).__name__}"
    return True, "database available"


def output_directory_health():
    root = Path(settings.STATIC_PUBLISH_ROOT)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"output directory is not writable: {type(exc).__name__}"
    return True, f"output directory is writable: {root}"


def release_health():
    manifest_path = Path(settings.STATIC_PUBLISH_ROOT) / "current" / "manifest.json"
    if not manifest_path.is_file():
        return False, "active release manifest is missing"
    try:
        disk_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"active release manifest is invalid: {type(exc).__name__}"
    active = StaticManifest.objects.filter(is_active=True).only("version").first()
    if active is None:
        return False, "active release database record is missing"
    if disk_manifest.get("version") != active.version:
        return False, "active release manifest does not match the database"
    if disk_manifest.get("summary", {}).get("failed") != 0:
        return False, "active release reports failed pages"
    return True, f"active release {active.version} is ready"


def broker_health():
    timeout = float(getattr(settings, "STATIC_PUBLISH_BROKER_HEALTHCHECK_TIMEOUT", 2))
    try:
        with Connection(settings.CELERY_BROKER_URL, connect_timeout=timeout) as broker:
            broker.connect()
    except Exception as exc:
        return False, f"task broker unavailable: {type(exc).__name__}"
    return True, "task broker available"


def get_health_report(*, include_release=False, include_broker=False):
    checks = {
        "database": database_health(),
        "output": output_directory_health(),
    }
    if include_release:
        checks["release"] = release_health()
    if include_broker:
        checks["broker"] = broker_health()
    return {
        "status": "ok" if all(result[0] for result in checks.values()) else "error",
        "checks": {
            name: {"ok": result[0], "message": result[1]}
            for name, result in checks.items()
        },
    }
