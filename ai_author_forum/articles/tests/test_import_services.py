import io
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    ArticleImportValidationError,
    confirm_article_import,
    execute_confirmed_article_import,
    preview_article_import,
)
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalStatus,
    StaticArticle,
)
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.test_helpers import grant_business_super_admin


def csv_upload(rows, name="articles.csv"):
    header = "journal_slug,title,slug,article_type,authors,body_html,notes\n"
    content = header + "\n".join(rows) + "\n"
    return SimpleUploadedFile(
        name, content.encode("utf-8-sig"), content_type="text/csv"
    )


class ArticleImportServiceTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self.override.enable()
        self.user = get_user_model().objects.create_superuser(
            username="article-import-admin", email="admin@example.com", password="test"
        )
        grant_business_super_admin(self.user)
        self.journal = Journal.objects.create(
            name="AI Journal",
            slug="ai-journal",
            az_group="A",
            status=JournalStatus.ACTIVE,
        )
        self.other = Journal.objects.create(
            name="Other Journal",
            slug="other-journal",
            az_group="O",
            status=JournalStatus.ACTIVE,
        )

    def tearDown(self):
        self.override.disable()
        self.tempdir.cleanup()

    def test_preview_does_not_write_business_data_and_execute_forces_draft(self):
        upload = csv_upload(
            [
                'ai-journal,Imported Article,imported-article,news,Author,"<h2>Heading</h2><p>Body</p>",note'
            ]
        )
        job = preview_article_import(
            upload,
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.user,
        )
        self.assertEqual(job.status, ImportJobStatus.READY)
        self.assertEqual(job.summary["created_rows"], 1)
        self.assertEqual(StaticArticle.objects.count(), 0)
        self.assertEqual(ArticlePage.objects.count(), 0)
        self.assertEqual(ArticlePlacement.objects.count(), 0)

        confirm_article_import(job, operator=self.user)
        execute_confirmed_article_import(job, operator=self.user)
        article = StaticArticle.objects.get(
            journal=self.journal, slug="imported-article"
        )
        page = ArticlePage.objects.get(source_static_article=article)
        self.assertEqual(article.review_status, "draft")
        self.assertEqual(article.build_version, "")
        self.assertFalse(article.is_pinned)
        self.assertEqual(page.review_status, "draft")
        self.assertEqual(page.publication_status, "")
        self.assertEqual(page.build_version, "")
        self.assertEqual(page.published_version, "")
        self.assertFalse(page.live)
        self.assertTrue(page.has_unpublished_changes)
        self.assertEqual(ArticlePlacement.objects.count(), 0)

    def test_duplicate_import_updates_by_journal_and_slug(self):
        first = csv_upload(
            ['ai-journal,First Title,same-slug,news,Author,"<p>First</p>",']
        )
        job = preview_article_import(
            first, context=ArticleImportContext(scope="global"), operator=self.user
        )
        confirm_article_import(job, operator=self.user)
        execute_confirmed_article_import(job, operator=self.user)
        second = csv_upload(
            ['ai-journal,Updated Title,same-slug,news,Author,"<p>Updated</p>",']
        )
        job = preview_article_import(
            second, context=ArticleImportContext(scope="global"), operator=self.user
        )
        self.assertEqual(job.summary["updated_rows"], 1)
        confirm_article_import(job, operator=self.user)
        execute_confirmed_article_import(job, operator=self.user)
        self.assertEqual(
            StaticArticle.objects.filter(
                journal=self.journal, slug="same-slug"
            ).count(),
            1,
        )
        self.assertEqual(
            StaticArticle.objects.get(slug="same-slug").title, "Updated Title"
        )

    def test_journal_scope_injects_target_and_rejects_cross_journal_row(self):
        upload = csv_upload(
            [
                ',Local Article,local-article,news,Author,"<p>Body</p>",',
                'other-journal,Wrong Journal,wrong-journal,news,Author,"<p>Body</p>",',
            ]
        )
        job = preview_article_import(
            upload,
            context=ArticleImportContext(
                scope="journal", target_journal_id=self.journal.pk
            ),
            operator=self.user,
        )
        self.assertEqual(job.summary["created_rows"], 1)
        self.assertEqual(job.summary["failed_rows"], 1)
        self.assertEqual(
            job.rows.get(row_no=3).error_code, "ARTICLE_JOURNAL_SCOPE_MISMATCH"
        )
        confirm_article_import(job, operator=self.user)
        execute_confirmed_article_import(job, operator=self.user)
        self.assertTrue(
            StaticArticle.objects.filter(
                journal=self.journal, slug="local-article"
            ).exists()
        )
        self.assertFalse(StaticArticle.objects.filter(slug="wrong-journal").exists())

    def test_dangerous_html_and_duplicate_rows_are_isolated(self):
        upload = csv_upload(
            [
                'ai-journal,Unsafe,unsafe,news,Author,"<script>alert(1)</script><p>Body</p>",',
                'ai-journal,Good,good,news,Author,"<p>Body</p>",',
                'ai-journal,Duplicate,good,news,Author,"<p>Body</p>",',
            ]
        )
        job = preview_article_import(
            upload, context=ArticleImportContext(scope="global"), operator=self.user
        )
        codes = set(
            job.rows.filter(status="failed").values_list("error_code", flat=True)
        )
        self.assertIn("ARTICLE_HTML_UNSAFE", codes)
        self.assertIn("ARTICLE_DUPLICATE_IN_FILE", codes)
        self.assertEqual(job.summary["created_rows"], 1)

    def test_delivered_mixed_package_fixture_previews_and_imports_three_drafts(self):
        fixture_journal = Journal.objects.create(
            name="Fixture Journal",
            slug="fixture-journal",
            az_group="F",
            status=JournalStatus.ACTIVE,
        )
        fixture = (
            Path(__file__).parent / "fixtures" / "document_import" / "package-mixed.zip"
        )
        upload = SimpleUploadedFile(
            fixture.name,
            fixture.read_bytes(),
            content_type="application/zip",
        )

        job = preview_article_import(
            upload,
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.user,
        )

        self.assertEqual(job.status, ImportJobStatus.READY)
        self.assertEqual(job.summary["created_rows"], 3)
        self.assertEqual(
            set(job.rows.values_list("source_format", flat=True)),
            {"docx", "markdown", "html"},
        )
        self.assertEqual(StaticArticle.objects.count(), 0)
        confirm_article_import(job, operator=self.user)
        execute_confirmed_article_import(job, operator=self.user)
        imported = StaticArticle.objects.filter(journal=fixture_journal)
        self.assertEqual(imported.count(), 3)
        self.assertEqual(
            set(imported.values_list("review_status", flat=True)), {"draft"}
        )
        self.assertEqual(ArticlePlacement.objects.count(), 0)

    def test_zip_path_traversal_is_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                "articles.csv",
                "journal_slug,title,slug,article_type,authors,body_html\n",
            )
            archive.writestr("../escape.html", "bad")
        upload = SimpleUploadedFile(
            "articles.zip", stream.getvalue(), content_type="application/zip"
        )
        with self.assertRaises(ArticleImportValidationError):
            preview_article_import(
                upload, context=ArticleImportContext(scope="global"), operator=self.user
            )

    def test_xlsx_template_is_supported(self):
        from ai_author_forum.articles.import_templates import build_article_import_xlsx

        data = build_article_import_xlsx(
            scope="journal", journal_slug=self.journal.slug
        )
        upload = SimpleUploadedFile(
            "articles.xlsx",
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        job = preview_article_import(
            upload,
            context=ArticleImportContext(
                scope="journal", target_journal_id=self.journal.pk
            ),
            operator=self.user,
        )
        self.assertEqual(job.template_version, 2)
        self.assertEqual(job.status, ImportJobStatus.READY)
