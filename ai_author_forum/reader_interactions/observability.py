"""Low-cardinality metrics and privacy-safe logs for the reader data plane."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import uuid4

from django.conf import settings
from django.db import connections
from django.db.models import Count
from django.utils import timezone
from redis import Redis

logger = logging.getLogger("ai_author_forum.reader_observability")

_REQUEST_ID_MAX_LENGTH = 80
_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_ALLOWED_QUEUES = ("reader_email", "reader_comments", "reader_pdf", "static_publish")


def request_id_for(request) -> str:
    existing = getattr(request, "reader_request_id", "")
    if existing:
        return existing
    supplied = request.headers.get("X-Request-ID", "")
    if (
        not supplied
        or len(supplied) > _REQUEST_ID_MAX_LENGTH
        or not all(char.isalnum() or char in "._:-" for char in supplied)
    ):
        supplied = str(uuid4())
    request.reader_request_id = supplied
    return supplied


def _route_for(request) -> str:
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", "") if match else ""
    if route:
        return "/" + route.strip("/") + "/"
    return "/reader-api/unmatched/"


@dataclass
class ReaderMetrics:
    """A per-process registry; Prometheus scrapes each replica separately."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    requests: dict = field(default_factory=lambda: defaultdict(int))
    errors: dict = field(default_factory=lambda: defaultdict(int))
    durations: dict = field(default_factory=lambda: defaultdict(list))
    inflight: int = 0
    scrape_errors: int = 0

    def begin(self):
        with self.lock:
            self.inflight += 1

    def finish(self, *, method, route, status, error_code, duration):
        key = (method, route, str(status))
        with self.lock:
            self.inflight = max(0, self.inflight - 1)
            self.requests[key] += 1
            self.durations[(method, route)].append(duration)
            if len(self.durations[(method, route)]) > 20_000:
                self.durations[(method, route)] = self.durations[(method, route)][
                    -10_000:
                ]
            if error_code:
                self.errors[(route, error_code)] += 1

    def snapshot(self):
        with self.lock:
            return {
                "requests": dict(self.requests),
                "errors": dict(self.errors),
                "durations": {
                    key: tuple(values) for key, values in self.durations.items()
                },
                "inflight": self.inflight,
                "scrape_errors": self.scrape_errors,
            }

    def record_scrape_error(self):
        with self.lock:
            self.scrape_errors += 1


reader_metrics = ReaderMetrics()


class ReaderObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/reader-api/"):
            return self.get_response(request)
        request_id = request_id_for(request)
        started = time.monotonic()
        reader_metrics.begin()
        response = None
        error_code = ""
        try:
            response = self.get_response(request)
            error_code = getattr(response, "reader_error_code", "")
            return response
        finally:
            duration = time.monotonic() - started
            route = _route_for(request)
            status = getattr(response, "status_code", 500)
            reader_metrics.finish(
                method=request.method,
                route=route,
                status=status,
                error_code=error_code or ("unhandled" if response is None else ""),
                duration=duration,
            )
            logger.info(
                "reader_api_request",
                extra={
                    "event": "reader_api_request",
                    "request_id": request_id,
                    "route": route,
                    "method": request.method,
                    "status": status,
                    "duration_ms": round(duration * 1000, 3),
                    "error_category": error_code or None,
                },
            )


def _labels(**values):
    escaped = []
    for key, value in values.items():
        encoded = str(value).replace("\\", "\\\\").replace('"', '\\"')
        escaped.append(f'{key}="{encoded}"')
    return "{" + ",".join(escaped) + "}"


def _metric(lines, name, value, **labels):
    lines.append(f"{name}{_labels(**labels) if labels else ''} {value}")


def _dependency_gauges(lines):
    for alias in ("default", "interactions"):
        try:
            connection = connections[alias]
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                if alias == "interactions":
                    _metric(
                        lines,
                        "reader_database_connection_limit",
                        settings.READER_INTERACTIONS_DB_CONNECTION_LIMIT,
                        database=alias,
                    )
                if connection.vendor == "postgresql":
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database()"
                    )
                    _metric(
                        lines,
                        "reader_database_connections",
                        int(cursor.fetchone()[0]),
                        database=alias,
                    )
            _metric(lines, "reader_dependency_up", 1, dependency=f"database_{alias}")
        except Exception:  # noqa: BLE001 - scrape exposes only dependency category
            _metric(lines, "reader_dependency_up", 0, dependency=f"database_{alias}")
            reader_metrics.record_scrape_error()

    redis_urls = {
        "rate_limit": settings.READER_RATE_LIMIT_REDIS_URL,
        "broker": settings.CELERY_BROKER_URL,
    }
    for dependency, url in redis_urls.items():
        try:
            client = Redis.from_url(
                url,
                socket_connect_timeout=1,
                socket_timeout=1,
                decode_responses=True,
            )
            client.ping()
            _metric(lines, "reader_dependency_up", 1, dependency=f"redis_{dependency}")
            if dependency == "broker":
                for queue in _ALLOWED_QUEUES:
                    _metric(
                        lines, "reader_queue_depth", client.llen(queue), queue=queue
                    )
                    oldest = client.lindex(queue, -1)
                    oldest_age = 0.0
                    if oldest:
                        with suppress(TypeError, ValueError, json.JSONDecodeError):
                            sent_at = float(
                                json.loads(oldest)
                                .get("headers", {})
                                .get("reader_enqueued_at", 0)
                            )
                            if sent_at > 0:
                                oldest_age = max(0.0, time.time() - sent_at)
                    _metric(
                        lines,
                        "reader_queue_oldest_age_seconds",
                        f"{oldest_age:.3f}",
                        queue=queue,
                    )
        except Exception:  # noqa: BLE001 - scrape exposes only dependency category
            _metric(lines, "reader_dependency_up", 0, dependency=f"redis_{dependency}")
            reader_metrics.record_scrape_error()


def _business_gauges(lines):
    try:
        from ai_author_forum.static_publish.models import StaticManifest

        from .models import (
            ArticleCapabilityProjection,
            Comment,
            CommentSnapshot,
            InteractionOutbox,
        )

        counts = dict(
            Comment.objects.values_list("state")
            .annotate(total=Count("id"))
            .values_list("state", "total")
        )
        for state in Comment.State.values:
            _metric(lines, "reader_comments", counts.get(state, 0), state=state)
        oldest_outbox = (
            InteractionOutbox.objects.filter(published_at__isnull=True)
            .order_by("created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        _metric(
            lines,
            "reader_outbox_oldest_age_seconds",
            f"{max(0.0, (timezone.now() - oldest_outbox).total_seconds()) if oldest_outbox else 0.0:.3f}",
        )
        latest_comment = (
            Comment.objects.order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first()
        )
        latest_snapshot = (
            CommentSnapshot.objects.order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        snapshot_lag = (
            max(0.0, (latest_comment - latest_snapshot).total_seconds())
            if latest_comment and latest_snapshot and latest_comment > latest_snapshot
            else (
                max(0.0, (timezone.now() - latest_comment).total_seconds())
                if latest_comment
                else 0.0
            )
        )
        _metric(lines, "reader_comment_snapshot_lag_seconds", f"{snapshot_lag:.3f}")

        active_release = (
            StaticManifest.objects.using("default")
            .filter(is_active=True)
            .values_list("version", flat=True)
            .get()
        )
        projected_releases = set(
            ArticleCapabilityProjection.objects.using("interactions")
            .exclude(active_release="")
            .values_list("active_release", flat=True)
            .distinct()
        )
        _metric(
            lines,
            "reader_manifest_projection_match",
            int(not projected_releases or projected_releases == {active_release}),
        )
    except Exception:  # noqa: BLE001 - dependency gauge already reports the outage
        _metric(lines, "reader_manifest_projection_match", 0)
        reader_metrics.record_scrape_error()

    try:
        from ai_author_forum.reader_access.models import (
            ModerationCommand,
            ProtectedArtifact,
        )

        _metric(
            lines,
            "reader_moderation_backlog",
            ModerationCommand.objects.filter(
                status__in=(
                    ModerationCommand.Status.PENDING,
                    ModerationCommand.Status.RUNNING,
                    ModerationCommand.Status.UNKNOWN,
                )
            ).count(),
        )
        for status in ProtectedArtifact.Status.values:
            _metric(
                lines,
                "reader_pdf_artifacts",
                ProtectedArtifact.objects.filter(status=status).count(),
                status=status,
            )
    except Exception:  # noqa: BLE001 - dependency gauge already reports the outage
        reader_metrics.record_scrape_error()


def render_metrics() -> str:
    snapshot = reader_metrics.snapshot()
    lines = [
        "# HELP reader_http_requests_total Reader API requests by stable route.",
        "# TYPE reader_http_requests_total counter",
    ]
    for (method, route, status), count in sorted(snapshot["requests"].items()):
        _metric(
            lines,
            "reader_http_requests_total",
            count,
            method=method,
            route=route,
            status=status,
        )
    lines.extend(
        (
            "# HELP reader_http_request_duration_seconds Reader API request latency.",
            "# TYPE reader_http_request_duration_seconds histogram",
        )
    )
    for (method, route), values in sorted(snapshot["durations"].items()):
        for boundary in _DURATION_BUCKETS:
            _metric(
                lines,
                "reader_http_request_duration_seconds_bucket",
                sum(value <= boundary for value in values),
                method=method,
                route=route,
                le=boundary,
            )
        _metric(
            lines,
            "reader_http_request_duration_seconds_bucket",
            len(values),
            method=method,
            route=route,
            le="+Inf",
        )
        _metric(
            lines,
            "reader_http_request_duration_seconds_sum",
            f"{sum(values):.9f}",
            method=method,
            route=route,
        )
        _metric(
            lines,
            "reader_http_request_duration_seconds_count",
            len(values),
            method=method,
            route=route,
        )
    for (route, code), count in sorted(snapshot["errors"].items()):
        _metric(lines, "reader_api_errors_total", count, route=route, code=code)
    _metric(lines, "reader_http_inflight", snapshot["inflight"])
    _dependency_gauges(lines)
    _business_gauges(lines)
    _metric(
        lines,
        "reader_observability_scrape_errors_total",
        reader_metrics.snapshot()["scrape_errors"],
    )
    return "\n".join(lines) + "\n"


_task_started = {}
_task_lock = threading.Lock()
_celery_signals_installed = False


def install_celery_observability():
    global _celery_signals_installed
    if _celery_signals_installed:
        return
    with suppress(ImportError):
        from celery.signals import before_task_publish, task_postrun, task_prerun

        @before_task_publish.connect(weak=False)
        def _before_task_publish(headers=None, **_kwargs):
            if headers is not None:
                headers["reader_enqueued_at"] = time.time()

        @task_prerun.connect(weak=False)
        def _task_prerun(task_id=None, **_kwargs):
            if task_id:
                with _task_lock:
                    _task_started[str(task_id)] = time.monotonic()

        @task_postrun.connect(weak=False)
        def _task_postrun(task_id=None, task=None, state=None, **_kwargs):
            with _task_lock:
                started = _task_started.pop(str(task_id), None)
            duration_ms = (
                round((time.monotonic() - started) * 1000, 3) if started else None
            )
            task_name = getattr(task, "name", "unknown")
            if not task_name.startswith("ai_author_forum."):
                task_name = "other"
            logger.info(
                "reader_task_completed",
                extra={
                    "event": "reader_task_completed",
                    "event_id": str(task_id or ""),
                    "task": task_name,
                    "status": str(state or "unknown").lower(),
                    "duration_ms": duration_ms,
                },
            )

        _celery_signals_installed = True
