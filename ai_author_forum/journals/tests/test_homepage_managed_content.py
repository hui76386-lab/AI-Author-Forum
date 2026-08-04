from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from PIL import Image as PillowImage

from ai_author_forum.images.models import CustomImage
from ai_author_forum.images.references import get_image_references
from ai_author_forum.journals.models import Journal
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.static_publish.readiness import ContentReadinessResult
from ai_author_forum.static_publish.services import StaticPublisher


def uploaded_image(name="hero.png"):
    stream = BytesIO()
    PillowImage.new("RGB", (900, 520), "#3478a8").save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class JournalHomepageManagedContentModelTests(TestCase):
    def test_hero_defaults_and_controlled_fields(self):
        journal = Journal(name="AI Journal", slug="ai-journal", az_group="A")
        journal.full_clean()

        self.assertEqual(journal.hero_kicker, "期刊主页")
        self.assertEqual(journal.hero_primary_cta_text, "探索人工智能文章")
        self.assertEqual(journal.hero_primary_cta_url, "/explore-content/ai-article/")
        self.assertFalse(journal.hero_quick_links)

    def test_homepage_intro_rejects_uncontrolled_html(self):
        journal = Journal(
            name="AI Journal",
            slug="ai-journal",
            az_group="A",
            homepage_intro="<p><script>alert(1)</script></p>",
        )

        with self.assertRaises(ValidationError):
            journal.full_clean()

    def test_homepage_intro_accepts_draftail_block_attributes(self):
        journal = Journal(
            name="AI Journal",
            slug="ai-journal-draftail",
            az_group="A",
            homepage_intro=(
                '<p data-block-key="abc12"><strong>加粗</strong></p>'
                '<ul><li data-block-key="def34">列表项</li></ul>'
            ),
        )

        journal.full_clean()

    def test_quick_links_validate_urls_and_limit_to_six(self):
        invalid = Journal(
            name="AI Journal",
            slug="ai-journal",
            az_group="A",
            hero_quick_links=[
                {
                    "type": "link",
                    "value": {
                        "label": "Unsafe",
                        "url": "javascript:alert(1)",
                        "open_in_new_tab": False,
                    },
                }
            ],
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

        too_many = Journal(
            name="AI Journal",
            slug="ai-journal-too-many",
            az_group="A",
            hero_quick_links=[
                {
                    "type": "link",
                    "value": {
                        "label": f"Link {index}",
                        "url": f"/links/{index}/",
                        "open_in_new_tab": False,
                    },
                }
                for index in range(7)
            ],
        )
        with self.assertRaises(ValidationError):
            too_many.full_clean()

    @override_settings(MEDIA_ROOT="")
    def test_hero_image_is_protected_and_listed_as_a_reference(self):
        with TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                image = CustomImage.objects.create(
                    title="Hero image",
                    file=uploaded_image(),
                )
                journal = Journal.objects.create(
                    name="AI Journal",
                    slug="ai-journal",
                    az_group="A",
                    hero_image=image,
                )

                references = get_image_references(image)
                self.assertTrue(
                    any(
                        reference.source == "journal.hero_image"
                        and reference.object_id == str(journal.pk)
                        for reference in references
                    )
                )
                with self.assertRaises(ProtectedError):
                    image.delete()


class JournalHomepageManagedContentTemplateTests(TestCase):
    def test_homepage_renders_managed_hero_content_and_keeps_fallbacks(self):
        journal = Journal.objects.create(
            name="AI Journal",
            name_cn="人工智能期刊",
            slug="ai-journal",
            az_group="A",
            homepage_intro="<p><strong>Managed introduction</strong></p>",
            hero_kicker="Featured issue",
            hero_primary_cta_text="Read the AI article",
            hero_primary_cta_url="/articles/ai-article/",
            hero_quick_links=[
                {
                    "type": "link",
                    "value": {
                        "label": "投稿指南",
                        "url": "/submission-guide/",
                        "open_in_new_tab": False,
                    },
                },
                {
                    "type": "link",
                    "value": {
                        "label": "Editorial policy",
                        "url": "https://example.com/policy",
                        "open_in_new_tab": True,
                    },
                },
            ],
        )
        response = Client().get(f"/journals/{journal.slug}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "人工智能期刊")
        self.assertContains(response, "Managed introduction")
        self.assertContains(response, 'href="/articles/ai-article/"')
        self.assertContains(response, "投稿指南")
        self.assertContains(
            response,
            'target="_blank" rel="noopener noreferrer"',
        )
        self.assertNotContains(response, "AI Article</span>")

    def test_empty_managed_fields_keep_default_hero_content(self):
        journal = Journal.objects.create(
            name="Fallback Journal",
            slug="fallback-journal",
            az_group="F",
        )
        response = Client().get(f"/journals/{journal.slug}/")

        self.assertContains(response, "探索人工智能文章")
        self.assertContains(response, "AI \u6587\u7ae0")
        self.assertContains(response, "\u6d4f\u89c8\u5168\u90e8\u671f\u520a")

class JournalHomepageManagedContentStaticPublishTests(TestCase):
    def setUp(self):
        readiness_patcher = patch(
            "ai_author_forum.static_publish.services.check_content_readiness",
            return_value=ContentReadinessResult(configured=True),
        )
        readiness_patcher.start()
        self.addCleanup(readiness_patcher.stop)

    def test_static_publish_renders_hero_and_copies_hero_image(self):
        with TemporaryDirectory() as media_root, TemporaryDirectory() as publish_root:
            with self.settings(MEDIA_ROOT=media_root, STATIC_PUBLISH_ROOT=publish_root):
                image = CustomImage.objects.create(
                    title="Hero image",
                    file=uploaded_image("managed-hero.png"),
                )
                journal = Journal.objects.create(
                    name="AI Journal",
                    slug="ai-journal",
                    az_group="A",
                    homepage_intro="<p>Published Hero content</p>",
                    hero_image=image,
                )
                job = StaticPublishJob.objects.create(
                    scope=StaticPublishJob.Scope.FULL,
                )

                StaticPublisher(publish_root).build(job)

                current = Path(publish_root) / "current"
                journal_html = (
                    current / "journals" / journal.slug / "index.html"
                ).read_text(encoding="utf-8")
                manifest = (current / "manifest.json").read_text(encoding="utf-8")

                self.assertIn("Published Hero content", journal_html)
                self.assertIn("/media/", journal_html)
                self.assertTrue(
                    any(
                        path.is_file()
                        for path in (current / "media").rglob("*")
                        if path.is_file()
                    )
                )
                self.assertIn(f"journals/{journal.slug}/index.html", manifest)
