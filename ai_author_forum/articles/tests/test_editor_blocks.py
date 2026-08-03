from django.test import SimpleTestCase

from ..blocks import ArticleBodyBlock, ParagraphBlock
from ..models import ArticlePage


class ArticleBodyEditorConfigurationTests(SimpleTestCase):
    def test_visual_paragraph_is_the_default_first_block(self):
        body_block = ArticleBodyBlock()

        self.assertEqual(
            list(body_block.child_blocks),
            [
                "paragraph",
                "heading",
                "image",
                "quote",
                "list",
                "table",
                "document",
                "html",
            ],
        )
        self.assertEqual(
            body_block.child_blocks["paragraph"].label,
            "正文段落（可视化编辑）",
        )
        self.assertEqual(
            body_block.child_blocks["html"].label,
            "高级：Raw HTML（需权限）",
        )

    def test_rich_text_editor_exposes_safe_visual_features(self):
        paragraph = ParagraphBlock()

        self.assertEqual(
            paragraph.features,
            [
                "h2",
                "h3",
                "h4",
                "bold",
                "italic",
                "ol",
                "ul",
                "hr",
                "link",
            ],
        )

    def test_model_body_field_uses_the_structured_editor(self):
        body_field = ArticlePage._meta.get_field("body")

        self.assertEqual(body_field.verbose_name, "正文")
        self.assertEqual(next(iter(body_field.stream_block.child_blocks)), "paragraph")
        self.assertIn("无需手写 HTML", body_field.help_text)
