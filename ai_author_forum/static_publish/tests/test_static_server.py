import json
from wsgiref.util import setup_testing_defaults

from django.test import SimpleTestCase

from ai_author_forum.static_publish.static_server import StaticReleaseApplication


class StaticReleaseApplicationTests(SimpleTestCase):
    @staticmethod
    def request(app, path, method="GET"):
        environ = {}
        setup_testing_defaults(environ)
        environ["PATH_INFO"] = path
        environ["REQUEST_METHOD"] = method
        response = {}

        def start_response(status, headers):
            response["status"] = status
            response["headers"] = dict(headers)

        response["body"] = b"".join(app(environ, start_response))
        return response

    def test_serves_fixed_html_and_manifest_without_django_settings_access(self):
        with self.settings():
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                from pathlib import Path

                current = Path(directory, "current")
                current.mkdir()
                (current / "index.html").write_text(
                    "<main>offline</main>", encoding="utf-8"
                )
                (current / "manifest.json").write_text(
                    json.dumps({"targets": []}), encoding="utf-8"
                )
                app = StaticReleaseApplication(directory)

                page = self.request(app, "/")
                manifest = self.request(app, "/manifest.json", method="HEAD")

        self.assertEqual(page["status"], "200 OK")
        self.assertEqual(page["body"], b"<main>offline</main>")
        self.assertEqual(page["headers"]["X-Static-Release"], "true")
        self.assertEqual(manifest["status"], "200 OK")
        self.assertEqual(manifest["body"], b"")

    def test_serves_markdown_as_utf8_text(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            current = Path(directory, "current")
            current.mkdir()
            markdown = "# ????\n\n????\n"
            (current / "document.md").write_bytes(markdown.encode("utf-8"))
            (current / "manifest.json").write_text("{}", encoding="utf-8")
            app = StaticReleaseApplication(directory)

            response = self.request(app, "/document.md")

        self.assertEqual(response["status"], "200 OK")
        self.assertEqual(
            response["headers"]["Content-Type"], "text/markdown; charset=utf-8"
        )
        self.assertEqual(response["body"], markdown.encode("utf-8"))

    def test_manifest_redirect_is_a_real_http_301(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            current = Path(directory, "current")
            redirect_page = current / "journals/journal/categories/old/index.html"
            redirect_page.parent.mkdir(parents=True)
            redirect_page.write_text("fallback redirect page", encoding="utf-8")
            (current / "manifest.json").write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "action": "redirect",
                                "status": "generated",
                                "http_status": 301,
                                "output_path": "journals/journal/categories/old/index.html",
                                "canonical_path": "/journals/journal/categories/new/",
                                "redirect_to": "/journals/journal/categories/new/",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            app = StaticReleaseApplication(directory)

            response = self.request(app, "/journals/journal/categories/old/")

        self.assertEqual(response["status"], "301 Moved Permanently")
        self.assertEqual(
            response["headers"]["Location"], "/journals/journal/categories/new/"
        )
        self.assertEqual(response["body"], b"")

    def test_missing_or_unsafe_target_never_falls_back_to_django(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            current = Path(directory, "current")
            current.mkdir()
            (current / "manifest.json").write_text("{}", encoding="utf-8")
            app = StaticReleaseApplication(directory)

            missing = self.request(app, "/missing/")
            unsafe = self.request(app, "/../secret")

        self.assertEqual(missing["status"], "503 Service Unavailable")
        self.assertEqual(unsafe["status"], "400 Bad Request")
