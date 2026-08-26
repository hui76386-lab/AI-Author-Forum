from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = ""
        self.release = ""
        self.article_id = ""
        self.full_text = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href", "")
        if values.get("id") == "full-text":
            self.full_text = True
        if values.get("id") == "reader-interactions":
            self.release = values.get("data-release", "")
            self.article_id = values.get("data-article-id", "")


def _fetch(base_url, path, *, timeout, host_header=""):
    headers = {
        "X-Forwarded-Proto": "https",
        "User-Agent": "reader-synthetic/1",
    }
    if host_header:
        headers["Host"] = host_header
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except URLError as exc:
        raise CommandError(
            f"Synthetic request failed: {type(exc.reason).__name__}"
        ) from exc


class Command(BaseCommand):
    help = "Run a no-email, read-only reader synthetic check."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True)
        parser.add_argument("--article-path", required=True)
        parser.add_argument("--expected-release", default="")
        parser.add_argument("--host-header", default="")
        parser.add_argument("--expect-reader-enabled", action="store_true")
        parser.add_argument("--timeout", type=float, default=5.0)

    def handle(self, *args, **options):
        checks = {}
        status, headers, body = _fetch(
            options["base_url"],
            options["article_path"],
            timeout=options["timeout"],
            host_header=options["host_header"],
        )
        parser = _ArticleParser()
        parser.feed(body.decode("utf-8"))
        checks["article"] = (
            status == 200
            and parser.full_text
            and bool(parser.canonical)
            and headers.get("X-Static-Served-By") == "nginx"
        )
        checks["release"] = bool(parser.release) and (
            not options["expected_release"]
            or parser.release == options["expected_release"]
        )

        status, headers, body = _fetch(
            options["base_url"],
            "/reader-api/v1/session/",
            timeout=options["timeout"],
            host_header=options["host_header"],
        )
        session = json.loads(body)
        checks["session"] = (
            status == 200
            and headers.get("Cache-Control") == "no-store"
            and session.get("data", {}).get("authenticated") is False
        )

        capability_path = f"/reader-api/v1/articles/{parser.article_id}/capabilities/"
        status, headers, _body = _fetch(
            options["base_url"],
            capability_path,
            timeout=options["timeout"],
            host_header=options["host_header"],
        )
        expected_status = 200 if options["expect_reader_enabled"] else 503
        checks["capability"] = (
            status == expected_status and headers.get("Cache-Control") == "no-store"
        )

        status, headers, _body = _fetch(
            options["base_url"],
            "/reader-api/v1/verify-email/",
            timeout=options["timeout"],
            host_header=options["host_header"],
        )
        checks["verification_no_send"] = (
            status == 200 and headers.get("Referrer-Policy") == "no-referrer"
        )
        status, _headers, _body = _fetch(
            options["base_url"],
            "/_protected_pdf/synthetic.pdf",
            timeout=options["timeout"],
            host_header=options["host_header"],
        )
        checks["protected_direct_denied"] = status == 404

        result = {
            "status": "ok" if all(checks.values()) else "error",
            "checks": checks,
            "release": parser.release,
        }
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["status"] != "ok":
            raise CommandError("Reader synthetic acceptance failed.")
