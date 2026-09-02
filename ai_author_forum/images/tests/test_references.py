import hashlib
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PillowImage
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal, StaticArticle
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.standardpages.models import IndexPage, StandardPage
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob
from ai_author_forum.static_publish.services import StaticPublisher
from ai_author_forum.test_helpers import grant_business_super_admin

from ..models import CustomImage
from ..references import (
    ImageReferenceProtectedError,
    get_image_asset_paths,
    get_image_references,
)


def uploaded_image(name="test.png", colour="red"):
    stream = BytesIO()
    PillowImage.new("RGB", (32, 24), colour).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class ImageTestMixin:
    def initialise_image_test_data(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.publish = TemporaryDirectory()
        self.addCleanup(self.publish.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media.name,
            STATIC_PUBLISH_ROOT=self.publish.name,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.journal = Journal.objects.create(
            name="Reference Journal",
            slug="reference-journal",
            az_group="R",
        )

    def create_image(self, name="test.png", colour="red"):
        return CustomImage.objects.create(
            title=name,
            file=uploaded_image(name, colour),
        )

    def create_article(self, title, slug, body):
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract=f"{title} abstract",
            body=body,
            authors="Editor",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=self.journal,
            keywords="images",
        )
        Page.get_first_root_node().add_child(instance=article)
        return ArticlePage.objects.get(pk=article.pk)

    def create_manifest(self, version, asset_references, *, active, files=None):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            status=StaticPublishJob.Status.SUCCEEDED,
            version=version,
        )
        return StaticManifest.objects.create(
            version=version,
            job=job,
            files=files or [],
            metadata={
                "schema_version": 1,
                "summary": {},
                "asset_references": asset_references,
            },
            is_active=active,
        )

    def assert_delete_is_protected(self, image, exception=ProtectedError):
        with self.assertRaises(exception) as error:
            with transaction.atomic():
                image.delete()
        return error


class ImageReferenceProtectionTests(ImageTestMixin, TestCase):
    def setUp(self):
        self.initialise_image_test_data()

    def test_unreferenced_image_can_be_deleted(self):
        image = self.create_image()
        image_id = image.pk

        image.delete()

        self.assertFalse(CustomImage.objects.filter(pk=image_id).exists())

    def test_journal_cover_image_prevents_deletion(self):
        image = self.create_image()
        self.journal.cover_image = image
        self.journal.save(update_fields=("cover_image",))

        self.assert_delete_is_protected(image)

        self.assertTrue(CustomImage.objects.filter(pk=image.pk).exists())

    def test_journal_metrics_image_prevents_deletion(self):
        image = self.create_image()
        self.journal.metrics_image = image
        self.journal.save(update_fields=("metrics_image",))

        self.assert_delete_is_protected(image)

    def test_article_featured_image_prevents_deletion(self):
        image = self.create_image()
        article = self.create_article(
            "Featured image article",
            "featured-image-article",
            [("paragraph", "Body")],
        )
        article.featured_image = image
        article.featured_image_alt = "Article cover"
        article.save(update_fields=("featured_image", "featured_image_alt"))

        self.assert_delete_is_protected(image)

        references = get_image_references(image)
        self.assertTrue(
            any(
                reference.label == "Article cover: Featured image article"
                and reference.field_name == "featured_image"
                for reference in references
            )
        )
        self.assertTrue(CustomImage.objects.filter(pk=image.pk).exists())

    def test_imported_article_html_image_prevents_deletion(self):
        image = self.create_image("imported-body.png")
        article = StaticArticle.objects.create(
            journal=self.journal,
            title="Imported body image",
            slug="imported-body-image",
        )
        article.html_source.save(
            "imported-body-image.html",
            ContentFile(f'<p>Body</p><img src="{image.file.url}" alt="Body">'.encode()),
            save=True,
        )

        error = self.assert_delete_is_protected(
            image, exception=ImageReferenceProtectedError
        )

        self.assertIn(
            "Imported article body: Imported body image", str(error.exception)
        )
        references = get_image_references(image)
        self.assertTrue(
            any(
                reference.source == "static_article.html_source"
                and reference.object_id == str(article.pk)
                for reference in references
            )
        )

    def test_placement_override_image_prevents_deletion(self):
        image = self.create_image()
        article = self.create_article(
            "Placement image article",
            "placement-image-article",
            [("paragraph", "Body")],
        )
        slot = LayoutSlot.objects.create(
            code="image_reference_slot",
            title="Image reference slot",
            scope=LayoutSlot.Scope.HOME,
        )
        ArticlePlacement.objects.create(
            slot=slot,
            article=article,
            override_image=image,
        )

        self.assert_delete_is_protected(image)

    def test_streamfield_image_prevents_deletion(self):
        image = self.create_image()
        self.create_article(
            "Stream image article",
            "stream-image-article",
            [("image", {"image": image, "caption": "Protected image"})],
        )

        error = self.assert_delete_is_protected(
            image, exception=ImageReferenceProtectedError
        )

        self.assertIn("Article body: Stream image article", str(error.exception))
        self.assertTrue(CustomImage.objects.filter(pk=image.pk).exists())

    def create_streamfield_page(self, page_class, *, title, slug, image):
        page = page_class(
            title=title,
            slug=slug,
            body=[
                (
                    "image",
                    {
                        "image": image,
                        "image_alt_text": "Protected image",
                        "caption": "Protected image",
                    },
                )
            ],
        )
        Page.get_first_root_node().add_child(instance=page)
        return page_class.objects.get(pk=page.pk)

    def assert_streamfield_page_image_is_protected(
        self, page_class, *, title, slug, expected_label, expected_source
    ):
        image = self.create_image(f"{slug}.png")
        page = self.create_streamfield_page(
            page_class, title=title, slug=slug, image=image
        )

        error = self.assert_delete_is_protected(
            image, exception=ImageReferenceProtectedError
        )

        self.assertIn(expected_label, str(error.exception))
        references = get_image_references(image)
        self.assertTrue(
            any(
                reference.source == expected_source
                and reference.object_id == str(page.pk)
                for reference in references
            )
        )

    def test_home_page_streamfield_image_prevents_deletion(self):
        self.assert_streamfield_page_image_is_protected(
            HomePage,
            title="Protected home page",
            slug="protected-home-page",
            expected_label="Home page body: Protected home page",
            expected_source="home.body",
        )

    def test_standard_page_streamfield_image_prevents_deletion(self):
        self.assert_streamfield_page_image_is_protected(
            StandardPage,
            title="Protected standard page",
            slug="protected-standard-page",
            expected_label="Standard page body: Protected standard page",
            expected_source="standard_page.body",
        )

    def test_index_page_streamfield_image_prevents_deletion(self):
        self.assert_streamfield_page_image_is_protected(
            IndexPage,
            title="Protected index page",
            slug="protected-index-page",
            expected_label="Index page body: Protected index page",
            expected_source="index_page.body",
        )

    def test_active_manifest_original_image_reference_prevents_deletion(self):
        image = self.create_image()
        asset_path = sorted(
            path for path in get_image_asset_paths(image) if path.startswith("media/")
        )[0]
        self.create_manifest(
            "release-original",
            [{"path": asset_path, "pages": ["articles/example/index.html"]}],
            active=True,
        )

        error = self.assert_delete_is_protected(
            image, exception=ImageReferenceProtectedError
        )

        self.assertIn("articles/example/index.html", str(error.exception))

    def test_active_manifest_rendition_reference_prevents_deletion(self):
        image = self.create_image()
        rendition = image.get_rendition("width-20")
        asset_path = f"media/{rendition.file.name}"
        self.create_manifest(
            "release-rendition",
            [{"path": asset_path, "pages": ["index.html"]}],
            active=True,
        )

        self.assert_delete_is_protected(image, exception=ImageReferenceProtectedError)

    def test_legacy_manifest_falls_back_to_current_release_html(self):
        image = self.create_image()
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            status=StaticPublishJob.Status.SUCCEEDED,
            version="legacy-release",
        )
        StaticManifest.objects.create(
            version=job.version,
            job=job,
            files=[],
            metadata={"schema_version": 1, "summary": {}},
            is_active=True,
        )
        current = Path(self.publish.name, "current")
        current.mkdir(parents=True)
        (current / "index.html").write_text(
            f'<html><img src="/media/{image.file.name}"></html>',
            encoding="utf-8",
        )

        self.assert_delete_is_protected(image, exception=ImageReferenceProtectedError)

    def test_inactive_manifest_does_not_prevent_deletion(self):
        image = self.create_image()
        asset_path = f"media/{image.file.name}"
        self.create_manifest(
            "release-old",
            [{"path": asset_path, "pages": ["old/index.html"]}],
            active=False,
        )
        self.create_manifest("release-active", [], active=True)

        image_id = image.pk
        image.delete()

        self.assertFalse(CustomImage.objects.filter(pk=image_id).exists())

    def test_rollback_changes_the_manifest_used_for_protection(self):
        image = self.create_image()
        asset_path = f"media/{image.file.name}"
        root = Path(self.publish.name)
        release = root / "releases" / "release-old"
        release.mkdir(parents=True)
        payload = b"old"
        (release / "index.html").write_bytes(payload)
        files = [
            {
                "path": "index.html",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ]
        old = self.create_manifest(
            "release-old",
            [{"path": asset_path, "pages": ["old/index.html"]}],
            active=False,
            files=files,
        )
        disk_manifest = {
            "version": old.version,
            "previous_version": old.previous_version,
            "files": old.files,
            **old.metadata,
        }
        (release / "manifest.json").write_text(
            json.dumps(disk_manifest), encoding="utf-8"
        )
        self.create_manifest("release-new", [], active=True)
        (root / "current").mkdir(parents=True)
        (root / "current" / "index.html").write_text("new", encoding="utf-8")

        actor = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="image-rollback-admin",
                email="image-rollback-admin@example.com",
                display_name="Image Rollback Admin",
                password="test-password",
                is_staff=True,
            )
        )
        StaticPublisher(self.publish.name).rollback(
            old.version,
            user=actor,
            reason="restore old image references",
        )

        self.assert_delete_is_protected(image, exception=ImageReferenceProtectedError)


class ImageAdminDeleteProtectionTests(ImageTestMixin, TestCase):
    def setUp(self):
        self.initialise_image_test_data()
        self.user = get_user_model().objects.create_superuser(
            username="image-admin",
            email="image-admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def protect_with_streamfield(self, image):
        self.create_article(
            "Admin protected image",
            "admin-protected-image",
            [("image", {"image": image, "caption": "Admin protection"})],
        )

    def test_single_delete_page_lists_references_and_hides_confirmation(self):
        image = self.create_image()
        self.protect_with_streamfield(image)

        response = self.client.get(reverse("wagtailimages:delete", args=(image.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "images/confirm_delete.html")
        self.assertContains(response, "Article body: Admin protected image")
        self.assertNotContains(response, "Yes, delete")

    def test_single_delete_post_keeps_referenced_image_with_clear_error(self):
        image = self.create_image()
        self.protect_with_streamfield(image)

        response = self.client.post(reverse("wagtailimages:delete", args=(image.pk,)))

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "still referenced", status_code=409)
        self.assertTrue(CustomImage.objects.filter(pk=image.pk).exists())

    def test_bulk_delete_hides_confirmation_and_keeps_referenced_image(self):
        image = self.create_image()
        self.protect_with_streamfield(image)
        next_url = reverse("wagtailimages:index")
        url = reverse(
            "wagtail_bulk_action",
            args=(CustomImage._meta.app_label, CustomImage._meta.model_name, "delete"),
        )
        url += "?" + urlencode({"id": image.pk, "next": next_url})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "images/confirm_bulk_delete.html")
        self.assertContains(response, "Article body: Admin protected image")
        self.assertNotContains(response, "Yes, delete")

        response = self.client.post(url)

        self.assertRedirects(response, next_url)
        self.assertTrue(CustomImage.objects.filter(pk=image.pk).exists())
