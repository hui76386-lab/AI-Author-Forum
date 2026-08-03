from __future__ import annotations

import csv
import io
import stat
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from PIL import Image as PillowImage

from ai_author_forum.articles.import_services import (
    ArticleImportContext,
    ArticleImportValidationError,
    preview_article_import,
)
from ai_author_forum.journals.models import (
    ArticleImportScope,
    Journal,
    JournalCategory,
    JournalStatus,
)

FIELDS = [
    "journal_slug",
    "title",
    "slug",
    "article_type",
    "authors",
    "body_html",
    "html_file",
    "cover_image",
    "primary_category_code",
    "related_category_codes",
]


def article_row(**overrides):
    row = {
        "journal_slug": "boundary-journal",
        "title": "Boundary article",
        "slug": "boundary-article",
        "article_type": "news",
        "authors": "Author",
        "body_html": "<p>Body</p>",
        "html_file": "",
        "cover_image": "",
        "primary_category_code": "",
        "related_category_codes": "",
    }
    row.update(overrides)
    return row


def csv_bytes(rows, *, encoding="utf-8-sig"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


def upload_csv(rows, *, encoding="utf-8-sig"):
    return SimpleUploadedFile(
        "articles.csv", csv_bytes(rows, encoding=encoding), content_type="text/csv"
    )


def upload_zip(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in entries:
            if isinstance(name, zipfile.ZipInfo):
                archive.writestr(name, content)
            else:
                archive.writestr(name, content)
    return SimpleUploadedFile(
        "articles.zip", stream.getvalue(), content_type="application/zip"
    )


def upload_xlsx(*, template_version, include_row=True):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "articles"
    sheet.append(FIELDS)
    if include_row:
        row = article_row()
        sheet.append([row[field] for field in FIELDS])
    metadata = workbook.create_sheet("_meta")
    metadata.append(["template_type", "article_import"])
    metadata.append(["template_version", template_version])
    metadata.append(["scope", "global"])
    metadata.sheet_state = "hidden"
    stream = io.BytesIO()
    workbook.save(stream)
    return SimpleUploadedFile(
        "articles.xlsx",
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def png_bytes():
    stream = io.BytesIO()
    PillowImage.new("RGB", (8, 8), "blue").save(stream, format="PNG")
    return stream.getvalue()


class ArticleImportBoundaryTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.tempdir.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.user = get_user_model().objects.create_superuser(
            username="article-import-boundary-admin",
            email="boundary@example.com",
            password="test",
        )
        self.journal = Journal.objects.create(
            name="Boundary Journal",
            slug="boundary-journal",
            az_group="B",
            status=JournalStatus.ACTIVE,
        )

    def preview(self, upload, *, csv_encoding="auto"):
        return preview_article_import(
            upload,
            context=ArticleImportContext(
                scope=ArticleImportScope.GLOBAL,
                csv_encoding=csv_encoding,
            ),
            operator=self.user,
        )

    def assert_package_error(self, upload, code):
        with self.assertRaises(ArticleImportValidationError) as error:
            self.preview(upload)
        self.assertEqual(error.exception.code, code)

    def test_gb18030_requires_explicit_encoding_and_then_imports(self):
        rows = [article_row(title="中文标题")]
        with self.assertRaises(ArticleImportValidationError) as error:
            self.preview(upload_csv(rows, encoding="gb18030"))
        self.assertEqual(error.exception.code, "ARTICLE_CSV_ENCODING_INVALID")

        job = self.preview(upload_csv(rows, encoding="gb18030"), csv_encoding="gb18030")
        self.assertEqual(job.summary["created_rows"], 1)
        self.assertEqual(job.rows.get().raw_data["title"], "中文标题")

    def test_template_versions_warn_reject_future_and_reject_negative(self):
        legacy = self.preview(upload_xlsx(template_version=0))
        self.assertEqual(legacy.template_version, 0)
        self.assertIn("兼容规则", legacy.summary["template_warning"])

        current = self.preview(upload_xlsx(template_version=2))
        self.assertEqual(current.template_version, 2)
        self.assertEqual(current.summary["template_warning"], "")

        self.assert_package_error(
            upload_xlsx(template_version=3), "ARTICLE_TEMPLATE_VERSION_UNSUPPORTED"
        )
        self.assert_package_error(
            upload_xlsx(template_version=-1), "ARTICLE_TEMPLATE_VERSION_INVALID"
        )

    def test_zip_file_count_member_total_and_symlink_limits(self):
        table = csv_bytes([article_row()])
        with patch("ai_author_forum.articles.import_services.MAX_ZIP_FILES", 1):
            self.assert_package_error(
                upload_zip([("articles.csv", table), ("extra.txt", b"x")]),
                "ARTICLE_ZIP_TOO_MANY_FILES",
            )

        with patch("ai_author_forum.articles.import_services.MAX_ZIP_MEMBER_SIZE", 20):
            self.assert_package_error(
                upload_zip([("oversize.bin", b"x" * 21), ("articles.csv", table)]),
                "ARTICLE_ZIP_MEMBER_TOO_LARGE",
            )

        with patch(
            "ai_author_forum.articles.import_services.MAX_ZIP_TOTAL_SIZE",
            len(table) + 5,
        ):
            self.assert_package_error(
                upload_zip([("articles.csv", table), ("extra.bin", b"x" * 6)]),
                "ARTICLE_ZIP_TOO_LARGE",
            )

        symlink = zipfile.ZipInfo("linked.html")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assert_package_error(
            upload_zip([(symlink, "target.html"), ("articles.csv", table)]),
            "ARTICLE_ZIP_SYMLINK",
        )

    def test_package_rejects_journal_configuration_and_html_path_traversal(self):
        table = csv_bytes([article_row()])
        self.assert_package_error(
            upload_zip([("articles.csv", table), ("journals.csv", b"slug\nwrong\n")]),
            "ARTICLE_PACKAGE_CONTAINS_JOURNALS",
        )

        traversal = csv_bytes(
            [article_row(body_html="", html_file="../outside/article.html")]
        )
        job = self.preview(upload_zip([("articles.csv", traversal)]))
        row = job.rows.get()
        self.assertEqual(row.error_code, "ARTICLE_HTML_FILE_NOT_FOUND")
        self.assertEqual(row.error_field, "html_file")

    def test_image_file_size_limit_is_enforced(self):
        table = csv_bytes([article_row(cover_image="images/cover.png")])
        with patch("ai_author_forum.articles.import_services.MAX_IMAGE_FILE_SIZE", 10):
            job = self.preview(
                upload_zip([("articles.csv", table), ("images/cover.png", png_bytes())])
            )
        self.assertEqual(job.rows.get().error_code, "ARTICLE_COVER_IMAGE_NOT_FOUND")

    def test_active_primary_category_cannot_be_repeated_as_related(self):
        JournalCategory.objects.create(
            journal=self.journal,
            name="Active category",
            code="ACTIVE",
            slug="active",
        )
        job = self.preview(
            upload_csv(
                [
                    article_row(
                        primary_category_code="ACTIVE",
                        related_category_codes="ACTIVE",
                    )
                ]
            )
        )
        self.assertEqual(job.rows.get().error_code, "ARTICLE_DUPLICATE_CATEGORY")
