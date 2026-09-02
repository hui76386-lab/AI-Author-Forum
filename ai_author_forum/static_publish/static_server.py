from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from wsgiref.simple_server import make_server

DEFAULT_ROOT = Path(os.environ.get("STATIC_PUBLISH_ROOT", "published"))
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)


class StaticReleaseApplication:
    """Serve one activated static release without importing Django or a database."""

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root or DEFAULT_ROOT).resolve()

    @property
    def current(self) -> Path:
        return self.root / "current"

    def __call__(self, environ, start_response):
        method = (environ.get("REQUEST_METHOD") or "GET").upper()
        if method not in {"GET", "HEAD"}:
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                b"Method Not Allowed\n",
                method=method,
                headers=[("Allow", "GET, HEAD")],
            )

        request_path = unquote(environ.get("PATH_INFO") or "/")
        if request_path == "/__static_health__/":
            payload = json.dumps(
                {
                    "status": "ok",
                    "release_available": (self.current / "manifest.json").is_file(),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            return self._respond(
                start_response,
                "200 OK",
                payload,
                method=method,
                content_type="application/json; charset=utf-8",
            )
        if request_path == "/.nginx-direct-ready" or request_path.startswith(
            "/.nginx-redirects/"
        ):
            return self._respond(
                start_response,
                "404 Not Found",
                b"Not Found\n",
                method=method,
            )

        relative = self._safe_request_path(request_path)
        if relative is None:
            return self._respond(
                start_response,
                "400 Bad Request",
                b"Bad Request\n",
                method=method,
            )

        manifest = self._load_manifest()
        redirect_to = self._redirect_location(manifest, request_path)
        if redirect_to:
            return self._respond(
                start_response,
                "301 Moved Permanently",
                b"",
                method=method,
                headers=[
                    ("Location", redirect_to),
                    ("Cache-Control", "public, max-age=300"),
                ],
            )

        candidate = self.current / relative
        if request_path.endswith("/") or candidate.is_dir():
            candidate = candidate / "index.html"
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.current.resolve())
        except (OSError, ValueError):
            return self._respond(
                start_response,
                "400 Bad Request",
                b"Bad Request\n",
                method=method,
            )
        if not resolved.is_file():
            return self._respond(
                start_response,
                "503 Service Unavailable",
                b"Static release target unavailable\n",
                method=method,
                headers=[("Retry-After", "60")],
            )

        content = resolved.read_bytes()
        content_type = (
            mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        )
        if resolved.suffix.lower() in {".md", ".markdown"}:
            content_type = "text/markdown"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "image/svg+xml",
        }:
            content_type = f"{content_type}; charset=utf-8"
        return self._respond(
            start_response,
            "200 OK",
            content,
            method=method,
            content_type=content_type,
            headers=[("X-Static-Release", "true")],
        )

    @staticmethod
    def _safe_request_path(request_path: str) -> Path | None:
        normalized = request_path.replace("\\", "/").lstrip("/")
        pure_path = PurePosixPath(normalized)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            return None
        return Path(*pure_path.parts) if pure_path.parts else Path()

    def _load_manifest(self) -> dict:
        try:
            return json.loads(
                (self.current / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _redirect_location(manifest: dict, request_path: str) -> str:
        normalized = "/" + request_path.lstrip("/")
        if normalized != "/" and not normalized.endswith("/"):
            normalized += "/"
        for target in manifest.get("targets", ()):
            output_path = str(target.get("output_path") or "").replace("\\", "/")
            if output_path.endswith("/index.html"):
                source_path = f"/{output_path[:-len('index.html')]}"
            else:
                source_path = f"/{output_path.lstrip('/')}"
            if (
                target.get("action") == "redirect"
                and target.get("status") == "generated"
                and target.get("http_status") == 301
                and source_path == normalized
            ):
                destination = str(target.get("redirect_to") or "")
                if destination.startswith("/") and not destination.startswith("//"):
                    return destination
        return ""

    @staticmethod
    def _respond(
        start_response,
        status: str,
        content: bytes,
        *,
        method: str,
        content_type: str = "text/plain; charset=utf-8",
        headers=None,
    ):
        response_headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(content))),
            ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
            ("X-Content-Type-Options", "nosniff"),
        ]
        response_headers.extend(headers or ())
        start_response(status, response_headers)
        return [b"" if method == "HEAD" else content]


application = StaticReleaseApplication()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve an activated fixed-HTML release without Django or database access."
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    options = parser.parse_args()
    app = StaticReleaseApplication(options.root)
    with make_server(options.host, options.port, app) as server:
        print(
            f"Serving static release {app.current} on "
            f"http://{options.host}:{options.port}",
            flush=True,
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
