from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import ArticleImportJob, Journal, StaticArticle
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.models import (
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
)


class SeedJournalDemoDataCommandTests(TestCase):
    def test_seed_command_creates_repeatable_static_article_demo_data(self):
        call_command(
            "seed_journal_demo_data",
            "--journals",
            "3",
            "--articles-per-journal",
            "4",
            "--prefix",
            "acceptance",
            "--home-feature-count",
            "5",
            stdout=StringIO(),
        )

        self.assertEqual(Journal.objects.count(), 3)
        self.assertEqual(StaticArticle.objects.count(), 12)
        self.assertEqual(ArticlePage.objects.count(), 12)
        self.assertEqual(ArticlePlacement.objects.count(), 0)
        self.assertEqual(
            NavigationSet.objects.filter(
                journal__slug__startswith="acceptance-journal-",
                scope=NavigationScope.JOURNAL,
                status=NavigationSetStatus.ACTIVE,
                is_template=False,
            ).count(),
            3,
        )
        html_source_field = StaticArticle._meta.get_field("html_source")
        self.assertTrue(
            all(
                len(article.html_source.name or "") <= html_source_field.max_length
                for article in StaticArticle.objects.all()
            )
        )
        self.assertEqual(
            ArticlePage.objects.filter(
                review_status=ArticlePage.ReviewStatus.DRAFT,
                publication_status="",
                live=False,
            ).count(),
            12,
        )

        call_command(
            "seed_journal_demo_data",
            "--journals",
            "3",
            "--articles-per-journal",
            "4",
            "--prefix",
            "acceptance",
            "--home-feature-count",
            "5",
            stdout=StringIO(),
        )

        self.assertEqual(Journal.objects.count(), 3)
        self.assertEqual(StaticArticle.objects.count(), 12)
        self.assertEqual(ArticlePage.objects.count(), 12)
        self.assertEqual(
            ArticleImportJob.objects.order_by("-pk").first().summary["updated"],
            12,
        )

    def test_seed_dry_run_does_not_create_business_records(self):
        call_command(
            "seed_journal_demo_data",
            "--journals",
            "2",
            "--articles-per-journal",
            "3",
            "--prefix",
            "preview",
            "--dry-run",
            stdout=StringIO(),
        )

        self.assertEqual(Journal.objects.count(), 0)
        self.assertEqual(StaticArticle.objects.count(), 0)
        self.assertEqual(ArticlePage.objects.count(), 0)
        self.assertEqual(ArticleImportJob.objects.get().summary["created"], 6)

    def test_seed_command_rejects_journal_counts_beyond_reserved_capacity(self):
        with self.assertRaisesMessage(CommandError, "between 1 and 200"):
            call_command(
                "seed_journal_demo_data",
                "--journals",
                "201",
                stdout=StringIO(),
            )

    def test_seed_command_rejects_invalid_prefix(self):
        with self.assertRaisesMessage(CommandError, "prefix must use"):
            call_command(
                "seed_journal_demo_data",
                "--prefix",
                "Bad Prefix",
                stdout=StringIO(),
            )
