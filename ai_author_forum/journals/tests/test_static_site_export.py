from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from openpyxl import Workbook

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.articles.services import sync_imported_article
from ai_author_forum.journals.models import Journal, JournalCategory, StaticArticle
from ai_author_forum.journals.services import import_package
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot


class StaticSiteExportTests(TestCase):
    def _build_package(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as zf:
            journal_wb = Workbook()
            ws = journal_wb.active
            ws.append(
                [
                    "journal_name",
                    "journal_name_cn",
                    "slug",
                    "az_group",
                    "status",
                    "sort_order",
                ]
            )
            ws.append(
                ["AI Journal 001", "AI Journal 001", "ai-journal-001", "A", "active", 1]
            )
            ws.append(
                ["AI Journal 002", "AI Journal 002", "ai-journal-002", "B", "active", 2]
            )
            journal_stream = BytesIO()
            journal_wb.save(journal_stream)
            zf.writestr("journals.xlsx", journal_stream.getvalue())

            article_wb = Workbook()
            ws = article_wb.active
            ws.append(
                [
                    "journal_slug",
                    "title",
                    "slug",
                    "article_type",
                    "status",
                    "body_html",
                    "main_site_slot",
                    "main_site_slot_name",
                    "main_site_slot_layout",
                    "main_site_slot_order",
                    "main_site_slot_pinned",
                    "main_site_slot_title",
                    "main_site_slot_summary",
                    "journal_slot",
                    "journal_slot_name",
                    "journal_slot_layout",
                    "journal_slot_order",
                    "journal_slot_pinned",
                    "journal_slot_title",
                    "journal_slot_summary",
                ]
            )
            ws.append(
                [
                    "ai-journal-001",
                    "Static AI article 001",
                    "static-ai-article-001",
                    "ai_article",
                    "published",
                    "<html><body><h1>Static AI article 001</h1></body></html>",
                    "home-feature-001",
                    "Home feature 001",
                    "hero",
                    1,
                    True,
                    "Static AI article 001",
                    "Summary 1",
                    "journal-feature-001",
                    "Journal feature 001",
                    "grid",
                    1,
                    True,
                    "Static AI article 001",
                    "Summary 1",
                ]
            )
            ws.append(
                [
                    "ai-journal-001",
                    "Static AI article 002",
                    "static-ai-article-002",
                    "ai_article",
                    "published",
                    "<html><body><h1>Static AI article 002</h1></body></html>",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "journal-feature-001-secondary",
                    "Journal secondary 001",
                    "list",
                    2,
                    False,
                    "Static AI article 002",
                    "Summary 2",
                ]
            )
            article_stream = BytesIO()
            article_wb.save(article_stream)
            zf.writestr("articles.xlsx", article_stream.getvalue())
        buffer.seek(0)
        buffer.name = "bundle.zip"
        return buffer

    def _approve_and_place_imported_articles(self):
        for source in StaticArticle.objects.select_related("journal").order_by("pk"):
            sync_imported_article(source)
        journal_slot, _ = LayoutSlot.objects.get_or_create(
            code="journal-static-export",
            defaults={
                "title": "Journal static export",
                "scope": LayoutSlot.Scope.JOURNAL,
                "max_items": 20,
            },
        )
        home_slot, _ = LayoutSlot.objects.get_or_create(
            code="home-static-export",
            defaults={
                "title": "Home static export",
                "scope": LayoutSlot.Scope.HOME,
                "max_items": 20,
            },
        )
        for index, article in enumerate(ArticlePage.objects.order_by("pk"), start=1):
            category, _ = JournalCategory.objects.get_or_create(
                journal=article.primary_journal,
                code="GENERAL",
                defaults={
                    "name": "General",
                    "slug": "general",
                    "depth": 1,
                    "path_cache": "general",
                },
            )
            ArticleCategoryAssignment.objects.update_or_create(
                article=article,
                category=category,
                defaults={"is_primary": True, "sort_order": 0},
            )
            revision = article.save_revision(
                user=None, bypass_article_permission_check=True
            )
            revision.publish(user=None, skip_permission_checks=True)
            ArticlePage.objects.filter(pk=article.pk).update(
                review_status=ArticlePage.ReviewStatus.APPROVED
            )
            article.refresh_from_db()
            ArticlePlacement.objects.create(
                article=article,
                slot=journal_slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=article.primary_journal.slug,
            )
            if index == 1:
                ArticlePlacement.objects.create(
                    article=article,
                    slot=home_slot,
                    target_type=ArticlePlacement.TargetType.MAIN_SITE,
                    target_slug="",
                    is_pinned=True,
                )

    def test_export_command_renders_static_html_tree(self):
        package = self._build_package()
        result = import_package(package, operator=None)
        self._approve_and_place_imported_articles()
        self.assertEqual(result.article_created, 2)
        self.assertTrue(
            ArticlePlacement.objects.filter(
                article__source_static_article__isnull=False,
                target_type=ArticlePlacement.TargetType.MAIN_SITE,
                is_pinned=True,
            ).exists()
        )

        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_dir = Path(temp_dir) / "static-site"
            with override_settings(
                AI_AUTHOR_FORUM_STATIC_SITE_ROOT=str(Path(temp_dir))
            ):
                call_command(
                    "build_static_journal_site",
                    "--output-dir",
                    str(output_dir),
                    "--clear",
                )

            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue(
                (output_dir / "journals" / "ai-journal-001" / "index.html").exists()
            )
            self.assertTrue(
                (
                    output_dir / "articles" / "static-ai-article-001" / "index.html"
                ).exists()
            )

    def test_export_command_does_not_render_an_unplaced_approved_article(self):
        import_package(self._build_package(), operator=None)
        self._approve_and_place_imported_articles()
        article = ArticlePage.objects.get(static_slug="static-ai-article-002")
        source = article.source_static_article
        ArticlePlacement.objects.filter(article=article).delete()

        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_dir = Path(temp_dir) / "static-site"
            with override_settings(
                AI_AUTHOR_FORUM_STATIC_SITE_ROOT=str(Path(temp_dir))
            ):
                call_command(
                    "build_static_journal_site",
                    "--output-dir",
                    str(output_dir),
                    "--clear",
                )

            self.assertFalse(
                (output_dir / "articles" / article.static_slug / "index.html").exists()
            )
            journal_html = (
                output_dir / "journals" / source.journal.slug / "index.html"
            ).read_text(encoding="utf-8")
            self.assertNotIn(article.title, journal_html)

    def test_export_command_isolates_journal_articles_by_target_slug(self):
        import_package(self._build_package(), operator=None)
        self._approve_and_place_imported_articles()
        first_journal = Journal.objects.get(slug="ai-journal-001")
        second_journal = Journal.objects.get(slug="ai-journal-002")
        first_article = ArticlePage.objects.get(static_slug="static-ai-article-001")
        second_article = ArticlePage.objects.get(static_slug="static-ai-article-002")
        first_article.related_journals.add(second_journal)

        first_placement = ArticlePlacement.objects.get(
            article=first_article,
            target_type=ArticlePlacement.TargetType.JOURNAL,
        )
        first_placement.target_slug = second_journal.slug
        first_placement.save(update_fields=("target_slug",))

        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_dir = Path(temp_dir) / "static-site"
            with override_settings(
                AI_AUTHOR_FORUM_STATIC_SITE_ROOT=str(Path(temp_dir))
            ):
                call_command(
                    "build_static_journal_site",
                    "--output-dir",
                    str(output_dir),
                    "--clear",
                )

            first_html = (
                output_dir / "journals" / first_journal.slug / "index.html"
            ).read_text(encoding="utf-8")
            second_html = (
                output_dir / "journals" / second_journal.slug / "index.html"
            ).read_text(encoding="utf-8")

        self.assertNotIn(first_article.title, first_html)
        self.assertIn(second_article.title, first_html)
        self.assertIn(first_article.title, second_html)
        self.assertNotIn(second_article.title, second_html)

    def test_export_command_rejects_output_dir_outside_static_root(self):
        Journal.objects.create(
            name="AI Journal 001", slug="ai-journal-001", az_group="A"
        )
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            root = Path(temp_dir) / "static-root"
            outside = Path(temp_dir) / "outside"
            with override_settings(AI_AUTHOR_FORUM_STATIC_SITE_ROOT=str(root)):
                with self.assertRaises(CommandError):
                    call_command(
                        "build_static_journal_site",
                        "--output-dir",
                        str(outside),
                        "--clear",
                    )

    def test_export_command_ignores_a_corrupt_legacy_article_path(self):
        import_package(self._build_package(), operator=None)
        self._approve_and_place_imported_articles()
        source = StaticArticle.objects.get(slug="static-ai-article-001")
        StaticArticle.objects.filter(pk=source.pk).update(
            static_output_path="../../unsafe.html"
        )
        article = ArticlePage.objects.get(source_static_article=source)

        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_dir = Path(temp_dir) / "static-site"
            with override_settings(
                AI_AUTHOR_FORUM_STATIC_SITE_ROOT=str(Path(temp_dir))
            ):
                call_command(
                    "build_static_journal_site",
                    "--output-dir",
                    str(output_dir),
                    "--clear",
                )

            self.assertTrue(
                (output_dir / "articles" / article.static_slug / "index.html").is_file()
            )
            self.assertFalse((Path(temp_dir) / "unsafe.html").exists())
