from __future__ import annotations

import json
import math
import os
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import cycle, islice
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError

from ai_author_forum.static_publish.models import StaticManifest


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _request(url, timeout, host_header=""):
    started = time.perf_counter()
    headers = {
        "X-Forwarded-Proto": "https",
        "User-Agent": "reader-capacity/1",
    }
    if host_header:
        headers["Host"] = host_header
    request = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except HTTPError as exc:
        exc.read()
        status = exc.code
    except (URLError, TimeoutError, OSError):
        status = 0
    return status, time.perf_counter() - started


class Command(BaseCommand):
    help = "Run a repeatable read-only capacity staircase and emit JSON evidence."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True)
        parser.add_argument(
            "--profile",
            choices=("static-hotspot", "static-cold", "api-session", "mixed"),
            required=True,
        )
        parser.add_argument("--article-path", default="/")
        parser.add_argument("--host-header", default="")
        parser.add_argument("--steps", default="1,4,16,32,64")
        parser.add_argument("--requests-per-worker", type=int, default=20)
        parser.add_argument("--p95-limit-ms", type=float, default=500.0)
        parser.add_argument("--error-limit", type=float, default=0.01)
        parser.add_argument("--timeout", type=float, default=5.0)

    def handle(self, *args, **options):
        try:
            steps = [int(value) for value in options["steps"].split(",")]
        except ValueError as exc:
            raise CommandError("--steps must be comma-separated integers") from exc
        if not steps or any(value < 1 for value in steps):
            raise CommandError("All concurrency steps must be positive")

        manifest = StaticManifest.objects.filter(is_active=True).first()
        release = manifest.version if manifest else ""
        static_paths = [options["article_path"]]
        if options["profile"] == "static-cold" and manifest:
            static_paths = [
                "/" + item["path"][: -len("index.html")]
                for item in manifest.files
                if item.get("path", "").startswith(("articles/", "en/articles/"))
                and item.get("path", "").endswith("index.html")
            ][:1000]
        if not static_paths:
            raise CommandError("No static article paths are available for the profile")

        report = {
            "profile": options["profile"],
            "base_url": options["base_url"],
            "release": release,
            "environment": {
                "cpu_count": os.cpu_count(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "active_manifest_files": len(manifest.files) if manifest else 0,
            },
            "thresholds": {
                "p95_ms": options["p95_limit_ms"],
                "error_rate": options["error_limit"],
            },
            "steps": [],
        }
        saturation = None
        for concurrency in steps:
            total = concurrency * options["requests_per_worker"]
            if options["profile"] == "api-session":
                paths = ["/reader-api/v1/session/"] * total
            elif options["profile"] == "mixed":
                paths = [
                    "/reader-api/v1/session/" if index % 5 == 0 else static_paths[0]
                    for index in range(total)
                ]
            else:
                paths = list(islice(cycle(static_paths), total))
            urls = [
                urljoin(options["base_url"].rstrip("/") + "/", path.lstrip("/"))
                for path in paths
            ]
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                results = list(
                    as_completed(
                        [
                            executor.submit(
                                _request,
                                url,
                                options["timeout"],
                                options["host_header"],
                            )
                            for url in urls
                        ]
                    )
                )
                samples = [future.result() for future in results]
            elapsed = time.perf_counter() - started
            latencies = [duration * 1000 for _status, duration in samples]
            # Capacity is about usable responses. WAF throttling (429) and every
            # other 4xx must therefore count as saturation, not as success.
            failures = sum(not 200 <= status < 400 for status, _duration in samples)
            error_rate = failures / total
            step = {
                "concurrency": concurrency,
                "requests": total,
                "rps": round(total / elapsed, 3),
                "error_rate": round(error_rate, 6),
                "p50_ms": round(statistics.median(latencies), 3),
                "p95_ms": round(_percentile(latencies, 0.95), 3),
                "p99_ms": round(_percentile(latencies, 0.99), 3),
            }
            step["saturated"] = (
                error_rate > options["error_limit"]
                or step["p95_ms"] > options["p95_limit_ms"]
            )
            report["steps"].append(step)
            if step["saturated"]:
                saturation = step
                break
        reference = saturation or report["steps"][-1]
        report["saturation"] = {
            "observed": saturation is not None,
            "concurrency": reference["concurrency"],
            "rps": reference["rps"],
            "sustained_50_percent_rps": round(reference["rps"] * 0.5, 3),
            "peak_70_percent_rps": round(reference["rps"] * 0.7, 3),
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
