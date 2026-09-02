from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image as PillowImage

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    ArticleImportValidationError,
    confirm_article_import,
    execute_confirmed_article_import,
    preview_article_import,
)
from ai_author_forum.journals.editor_services import (
    appoint_journal_editor,
    end_journal_editor_assignment,
)
from ai_author_forum.journals.models import (
    ArticleImportScope,
    ImportJobStatus,
    Journal,
    JournalCategory,
    JournalCategoryStatus,
    JournalEditorAssignment,
    JournalStatus,
    StaticArticle,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.test_helpers import grant_business_super_admin

BASE_FIELDS = [
    "journal_slug",
    "title",
    "slug",
    "article_type",
    "authors",
    "body_html",
    "html_file",
    "cover_image",
    "primary_category_code",
    "primary_category_path",
    "related_category_codes",
    "related_category_paths",
    "notes",
]


def csv_upload(rows, *, name="articles.csv", encoding="utf-8-sig"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=BASE_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return SimpleUploadedFile(
        name, stream.getvalue().encode(encoding), content_type="text/csv"
    )


def zip_upload(files, *, name="articles.zip"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return SimpleUploadedFile(name, stream.getvalue(), content_type="application/zip")


def png_bytes(width=32, height=24):
    stream = io.BytesIO()
    PillowImage.new("RGB", (width, height), "blue").save(stream, format="PNG")
    return stream.getvalue()


def article_row(**overrides):
    row = {
        "journal_slug": "ai-journal",
        "title": "Imported article",
        "slug": "imported-article",
        "article_type": "news",
        "authors": "Author",
        "body_html": "<p>Body</p>",
    }
    row.update(overrides)
    return row


class ArticleImportSecurityTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.tempdir.cleanup)
        self.admin = get_user_model().objects.create_superuser(
            username="article-import-security-admin",
            email="security@example.com",
            password="test",
        )
        grant_business_super_admin(self.admin)
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

    def preview(self, rows, *, context=None):
        return preview_article_import(
            csv_upload(rows),
            context=context or ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.admin,
        )

    def execute(self, job, *, allow_suspicious_text=None):
        confirm_article_import(
            job,
            operator=self.admin,
            allow_suspicious_text=bool(allow_suspicious_text),
            override_reason=(
                "Verified against the trusted source file"
                if allow_suspicious_text
                else ""
            ),
        )
        return execute_confirmed_article_import(
            job,
            operator=self.admin,
            allow_suspicious_text=allow_suspicious_text,
        )

    def test_unknown_type_missing_body_html_file_and_cover_are_reported_per_row(self):
        package = zip_upload(
            {
                "articles.csv": csv_upload(
                    [
                        article_row(slug="bad-type", article_type="unknown"),
                        article_row(slug="missing-body", body_html=""),
                        article_row(
                            slug="missing-html", body_html="", html_file="missing.html"
                        ),
                        article_row(slug="missing-cover", cover_image="missing.png"),
                    ]
                ).read(),
            }
        )
        job = preview_article_import(
            package,
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.admin,
        )

        rows = {row.row_no: row for row in job.rows.all()}
        self.assertEqual(rows[2].error_code, "ARTICLE_TYPE_INVALID")
        self.assertEqual(rows[2].error_field, "article_type")
        self.assertEqual(rows[3].error_code, "ARTICLE_BODY_MISSING")
        self.assertEqual(rows[4].error_code, "ARTICLE_HTML_FILE_NOT_FOUND")
        self.assertEqual(rows[5].error_code, "ARTICLE_COVER_IMAGE_NOT_FOUND")
        self.assertTrue(
            all(row.normalized_data.get("suggestion") for row in rows.values())
        )

        with job.error_report.open("rb") as report:
            header = report.readline().decode("utf-8-sig")
        self.assertIn("suggestion", header)

    def test_html_security_matrix_rejects_active_content_and_external_images(self):
        unsafe_html = {
            "script": "<script>alert(1)</script><p>Body</p>",
            "event": '<p onclick="alert(1)">Body</p>',
            "style": '<p style="color:red">Body</p>',
            "iframe": '<iframe src="https://example.com"></iframe>',
            "svg": '<svg><a xlink:href="javascript:alert(1)">x</a></svg>',
            "meta": '<meta http-equiv="refresh" content="0;url=https://example.com"><p>Body</p>',
            "scheme": '<a href="java&#10;script:alert(1)">Body</a>',
            "protocol-relative": '<a href="//example.com">Body</a>',
            "external-image": '<p>Body</p><img src="https://example.com/a.png">',
        }
        job = self.preview(
            [
                article_row(slug=f"unsafe-{index}", body_html=html)
                for index, html in enumerate(unsafe_html.values())
            ]
        )

        self.assertEqual(job.failed_rows, len(unsafe_html))
        self.assertEqual(
            set(job.rows.values_list("error_code", flat=True)),
            {"ARTICLE_HTML_UNSAFE"},
        )

    def test_clean_html_is_persisted_and_blank_target_is_hardened(self):
        job = self.preview(
            [
                article_row(
                    body_html=(
                        '<p data-untrusted="drop">Body</p>'
                        '<a href="https://example.com" target="_blank">Link</a>'
                    )
                )
            ]
        )
        self.execute(job)

        article = StaticArticle.objects.get(slug="imported-article")
        with article.html_source.open("rb") as source:
            html = source.read().decode("utf-8")
        self.assertNotIn("data-untrusted", html)
        self.assertIn('rel="noopener noreferrer"', html)
        row = job.rows.get()
        self.assertNotEqual(
            row.normalized_data["body_sha256"],
            row.normalized_data["sanitized_sha256"],
        )

    def test_zip_body_image_is_registered_and_rewritten_to_managed_media(self):
        csv_file = csv_upload(
            [
                article_row(
                    body_html="",
                    html_file="body/article.html",
                    cover_image="images/cover.png",
                )
            ]
        ).read()
        package = zip_upload(
            {
                "articles.csv": csv_file,
                "body/article.html": '<p>Body</p><img src="images/body.png">',
                "images/cover.png": png_bytes(),
                "images/body.png": png_bytes(),
            }
        )
        job = preview_article_import(
            package,
            context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
            operator=self.admin,
        )
        self.execute(job)
        result = job.rows.get()
        self.assertNotEqual(result.status, "failed", result.error_message)

        article = StaticArticle.objects.get(slug="imported-article")
        self.assertIsNotNone(article.cover_image_id)
        with article.html_source.open("rb") as source:
            html = source.read().decode("utf-8")
        self.assertIn("/media/", html)
        self.assertNotIn('src="images/body.png"', html)

    def test_image_dimension_limit_is_enforced(self):
        package = zip_upload(
            {
                "articles.csv": csv_upload(
                    [article_row(cover_image="images/oversize.png")]
                ).read(),
                "images/oversize.png": png_bytes(width=20, height=20),
            }
        )
        with patch("ai_author_forum.articles.import_services.MAX_IMAGE_WIDTH", 10):
            job = preview_article_import(
                package,
                context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
                operator=self.admin,
            )
        self.assertEqual(job.rows.get().error_code, "ARTICLE_COVER_IMAGE_NOT_FOUND")

    def test_category_not_found_cross_journal_inactive_and_duplicate_are_rejected(self):
        JournalCategory.objects.create(
            journal=self.journal,
            name="Inactive",
            code="INACTIVE",
            slug="inactive",
            status=JournalCategoryStatus.DISABLED,
        )
        JournalCategory.objects.create(
            journal=self.other,
            name="Other category",
            code="OTHER",
            slug="other-category",
        )
        job = self.preview(
            [
                article_row(slug="missing-category", primary_category_code="MISSING"),
                article_row(slug="cross-category", primary_category_code="OTHER"),
                article_row(slug="inactive-category", primary_category_code="INACTIVE"),
                article_row(
                    slug="duplicate-category",
                    primary_category_code="INACTIVE",
                    related_category_codes="INACTIVE",
                ),
            ]
        )
        codes = list(job.rows.values_list("error_code", flat=True))
        self.assertEqual(codes[0], "CATEGORY_NOT_FOUND")
        self.assertEqual(codes[1], "CATEGORY_CROSS_JOURNAL")
        self.assertEqual(codes[2], "CATEGORY_INACTIVE")
        self.assertEqual(codes[3], "CATEGORY_INACTIVE")

    def test_suspicious_text_blocks_preview_but_global_admin_can_confirm_with_reason(
        self,
    ):
        job = self.preview([article_row(title="Broken ??? title")])
        self.assertEqual(job.rows.get().error_code, "ARTICLE_TEXT_SUSPICIOUS")
        self.assertEqual(job.summary["suspicious_text_count"], 1)

        self.execute(job, allow_suspicious_text=True)

        self.assertTrue(StaticArticle.objects.filter(slug="imported-article").exists())
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(job.pk), message="强制按原文处理可疑文本"
            ).exists()
        )

    def test_formula_xlsx_is_rejected(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "articles"
        sheet.append(BASE_FIELDS)
        sheet.append(
            [
                "ai-journal",
                "Formula",
                "formula",
                "news",
                "Author",
                "=<p>Body</p>",
            ]
        )
        stream = io.BytesIO()
        workbook.save(stream)
        upload = SimpleUploadedFile(
            "articles.xlsx",
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        with self.assertRaises(ArticleImportValidationError) as error:
            preview_article_import(
                upload,
                context=ArticleImportContext(scope=ArticleImportScope.GLOBAL),
                operator=self.admin,
            )
        self.assertEqual(error.exception.code, "ARTICLE_XLSX_FORMULA")

    def test_row_limit_rejects_15001_rows_and_marks_preview_failed(self):
        rows = [article_row(slug=f"article-{index}") for index in range(15_001)]
        with self.assertRaises(ArticleImportValidationError) as error:
            self.preview(rows)
        self.assertEqual(error.exception.code, "ARTICLE_ROW_LIMIT")
        self.assertEqual(AuditLog.objects.filter(message="文章导入预览失败").count(), 1)

    def test_preview_confirm_and_execute_write_started_success_audit_events(self):
        job = self.preview([article_row()])
        self.execute(job)

        events = list(
            AuditLog.objects.filter(target_id=str(job.pk)).values_list(
                "message", "status"
            )
        )
        self.assertIn(("开始文章导入预览", "started"), events)
        self.assertIn(("文章导入预览完成", "success"), events)
        self.assertIn(("已确认文章导入", "started"), events)
        self.assertIn(("文章导入完成", "success"), events)

    def test_permission_revocation_moves_pending_job_to_failed(self):
        user = get_user_model().objects.create_user(
            username="revoked-importer",
            email="revoked-importer@example.com",
            display_name="Revoked Importer",
            password="test",
            is_staff=True,
        )
        assignment = appoint_journal_editor(
            actor=self.admin,
            user=user,
            journal=self.journal,
            role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            responsibilities=[
                JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
            ],
            public_profile={
                "public_name": user.display_name,
                "public_role_label": "副编辑",
            },
        )
        job = preview_article_import(
            csv_upload([article_row()]),
            context=ArticleImportContext(
                scope=ArticleImportScope.JOURNAL,
                target_journal_id=self.journal.pk,
            ),
            operator=user,
        )
        confirm_article_import(job, operator=user)
        end_journal_editor_assignment(
            actor=self.admin,
            assignment=assignment,
            reason="Permission revocation test.",
        )
        user = get_user_model().objects.get(pk=user.pk)

        with self.assertRaises(PermissionDenied):
            execute_confirmed_article_import(job, operator=user)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.FAILED)
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(job.pk),
                metadata__error_code="ARTICLE_IMPORT_PERMISSION_REVOKED",
            ).exists()
        )

    def test_duplicate_execution_does_not_rewrite_completed_terminal_state(self):
        job = self.preview([article_row()])
        self.execute(job)
        job.refresh_from_db()
        finished_at = job.finished_at

        with self.assertRaises(ArticleImportValidationError) as error:
            execute_confirmed_article_import(job, operator=self.admin)

        self.assertEqual(error.exception.code, "ARTICLE_IMPORT_STATE_INVALID")
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJobStatus.COMPLETED)
        self.assertEqual(job.finished_at, finished_at)
