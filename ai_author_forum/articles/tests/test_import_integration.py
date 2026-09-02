from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    confirm_article_import,
    execute_confirmed_article_import,
    preview_article_import,
)
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalCategory,
    JournalStatus,
    StaticArticle,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider
from ai_author_forum.static_publish.services import StaticPublisher
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)

FIELDS = [
    "journal_slug",
    "title",
    "slug",
    "article_type",
    "authors",
    "body_html",
    "primary_category_code",
    "status",
    "review_status",
    "publication_status",
    "is_pinned",
    "build_version",
]


def article_csv(*, slug="integrated-article"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerow(
        {
            "journal_slug": "integration-journal",
            "title": "Integrated article",
            "slug": slug,
            "article_type": "news",
            "authors": "Author",
            "body_html": "<h2>Imported heading</h2><p>Imported body</p>",
            "primary_category_code": "NEWS",
            "status": "published",
            "review_status": "approved",
            "publication_status": "published",
            "is_pinned": "true",
            "build_version": "malicious-version",
        }
    )
    return stream.getvalue().encode("utf-8-sig")


def article_upload(*, slug="integrated-article"):
    return SimpleUploadedFile(
        "articles.csv", article_csv(slug=slug), content_type="text/csv"
    )


class ArticleImportIntegrationTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.media_root = Path(self.tempdir.name, "media")
        self.queue_root = Path(self.tempdir.name, "queue")
        self.static_root = Path(self.tempdir.name, "static-site")
        settings_override = override_settings(
            MEDIA_ROOT=str(self.media_root),
            AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT=str(self.queue_root),
            STATIC_PUBLISH_ROOT=str(self.static_root),
            STATIC_PUBLISH_ENFORCE_CONTENT_READINESS=False,
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.user = get_user_model().objects.create_superuser(
            username="article-import-integration-admin",
            email="integration@example.com",
            password="test",
        )
        grant_business_super_admin(self.user)
        self.journal = Journal.objects.create(
            name="Integration Journal",
            slug="integration-journal",
            az_group="I",
            status=JournalStatus.ACTIVE,
        )
        self.category = JournalCategory.objects.create(
            journal=self.journal,
            name="News",
            code="NEWS",
            slug="news",
            path_cache="news",
        )

    def import_article(self, *, slug="integrated-article"):
        job = preview_article_import(
            article_upload(slug=slug),
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.user,
        )
        confirm_article_import(job, operator=self.user)
        execute_confirmed_article_import(job, operator=self.user)
        job.refresh_from_db()
        return job

    def test_upload_preview_confirm_execute_poll_and_journal_list(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("article_admin:import"),
            {"source_file": article_upload(), "csv_encoding": "auto"},
        )
        self.assertEqual(response.status_code, 302)
        job = ArticleImportJob.objects.get()
        self.assertEqual(
            response.url, f"{reverse('article_admin:import')}?job={job.pk}"
        )
        preview_response = self.client.get(response.url)
        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, f"#{job.pk}")
        self.assertEqual(job.status, ImportJobStatus.READY)
        self.assertEqual(StaticArticle.objects.count(), 0)
        self.assertEqual(ArticlePage.objects.count(), 0)
        self.assertEqual(ArticlePlacement.objects.count(), 0)
        self.assertEqual(StaticPublishJob.objects.count(), 0)

        with patch(
            "ai_author_forum.articles.import_views.start_article_import_process"
        ) as start_process:
            response = self.client.post(
                reverse("article_admin:import_confirm"), {"job_id": job.pk}
            )
        self.assertEqual(response.status_code, 302)
        start_process.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.PENDING)

        execute_confirmed_article_import(job, operator=self.user)
        status_response = self.client.get(
            reverse("article_admin:import_status"), {"job_id": job.pk}
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json()["terminal"])
        self.assertEqual(status_response.json()["status"], ImportJobStatus.COMPLETED)

        article = StaticArticle.objects.get(
            journal=self.journal, slug="integrated-article"
        )
        page = ArticlePage.objects.get(source_static_article=article)
        self.assertEqual(article.review_status, "draft")
        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertEqual(page.publication_status, "")
        self.assertFalse(page.live)
        self.assertFalse(article.is_pinned)
        self.assertEqual(article.build_version, "")
        self.assertEqual(ArticlePlacement.objects.count(), 0)
        self.assertEqual(StaticPublishJob.objects.count(), 0)

        list_response = self.client.get(
            reverse("article_admin:index"), {"primary_journal": self.journal.pk}
        )
        self.assertContains(list_response, "Integrated article")
        self.assertContains(list_response, page.get_review_status_display())

    def test_review_then_placement_then_static_publish_closes_business_loop(self):
        self.import_article()
        page = ArticlePage.objects.get(static_slug="integrated-article")
        article_url = f"/articles/{page.static_slug}/"

        formally_approve_test_article(page, actor=self.user)
        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.APPROVED)
        self.assertEqual(ArticlePlacement.objects.count(), 0)
        self.assertEqual(StaticPublishJob.objects.count(), 0)

        before_paths = {
            target.url for target in WagtailPageTargetProvider().get_targets()
        }
        self.assertNotIn(article_url, before_paths)

        slot = LayoutSlot.objects.create(
            title="Integration home list",
            code="integration-home-list",
            scope=LayoutSlot.Scope.HOME,
            max_items=10,
        )
        ArticlePlacement.objects.create(
            slot=slot,
            article=page,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
            placement_kind=ArticlePlacement.PlacementKind.FEATURED,
        )

        after_paths = {
            target.url for target in WagtailPageTargetProvider().get_targets()
        }
        self.assertIn(article_url, after_paths)

        publish_job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            triggered_by=self.user,
        )
        with patch.object(
            StaticPublisher, "_configure_snapshot_transaction", return_value=None
        ):
            StaticPublisher(self.static_root).build(publish_job)
        publish_job.refresh_from_db()
        self.assertEqual(publish_job.status, StaticPublishJob.Status.SUCCEEDED)
        article_file = (
            self.static_root / "current" / "articles" / page.static_slug / "index.html"
        )
        self.assertTrue(article_file.is_file())
        html = article_file.read_text(encoding="utf-8")
        self.assertIn("Integrated article", html)
        self.assertIn("Imported body", html)

    def test_reimport_of_reviewed_article_forces_it_back_to_draft_without_new_delivery(
        self,
    ):
        self.import_article(slug="reviewed-reimport")
        page = ArticlePage.objects.get(static_slug="reviewed-reimport")
        formally_approve_test_article(page, actor=self.user)
        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.APPROVED)

        second_job = self.import_article(slug="reviewed-reimport")
        page.refresh_from_db()
        source = page.source_static_article
        source.refresh_from_db()
        self.assertEqual(second_job.summary["updated_rows"], 1)
        self.assertEqual(source.review_status, "draft")
        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertEqual(page.publication_status, "")
        self.assertFalse(page.live)
        self.assertEqual(ArticlePlacement.objects.count(), 0)
        self.assertEqual(StaticPublishJob.objects.count(), 0)
