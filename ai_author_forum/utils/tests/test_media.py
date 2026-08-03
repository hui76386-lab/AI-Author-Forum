from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import RequestFactory, SimpleTestCase

from ai_author_forum.utils.media import serve_media


class MarkdownMediaServingTests(SimpleTestCase):
    databases = {"default"}

    def test_markdown_media_response_declares_utf8_charset(self):
        with TemporaryDirectory() as media_root:
            markdown_path = Path(media_root, "documents", "example.md")
            markdown_path.parent.mkdir(parents=True)
            content = "# 中文标题\n\n正文内容\n".encode()
            markdown_path.write_bytes(content)

            request = RequestFactory().get("/media/documents/example.md")
            response = serve_media(
                request,
                "documents/example.md",
                document_root=media_root,
            )
            body = b"".join(response.streaming_content)
            response.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(body, content)
