from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage as CanonicalArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.news.models import ArticlePage as LegacyArticlePage
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider
from ai_author_forum.utils.models import ArticleTopic, AuthorSnippet


class LegacyNewsArticleRetirementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = HomePage.objects.first() or Page.get_first_root_node()
        cls.news_listing = NewsListingPage(
            title="Legacy News",
            slug="legacy-news",
            introduction="Legacy listing.",
        )
        cls.home.add_child(instance=cls.news_listing)
        cls.news_listing.save_revision().publish()
        cls.author = AuthorSnippet.objects.create(title="Legacy Author")
        cls.topic = ArticleTopic.objects.create(title="Legacy Topic", slug="legacy")

    def test_legacy_news_article_is_not_creatable_under_listing_page(self):
        self.assertFalse(LegacyArticlePage.is_creatable)
        self.assertEqual(LegacyArticlePage.parent_page_types, [])
        self.assertEqual(NewsListingPage.subpage_types, [])

        article = LegacyArticlePage(
            title="Blocked legacy article",
            slug="blocked-legacy-article",
            author=self.author,
            topic=self.topic,
            introduction="Should not be created.",
            body=[],
        )

        with self.assertRaises(ValidationError):
            self.news_listing.add_child(instance=article)

    def test_legacy_news_article_does_not_render_or_publish_static_target(self):
        article = LegacyArticlePage(
            title="Existing legacy article",
            slug="existing-legacy-article",
            author=self.author,
            topic=self.topic,
            introduction="Old content.",
            body=[],
        )
        article._allow_legacy_article_save = True
        self.news_listing.add_child(instance=article)
        article.save_revision().publish()

        response = self.client.get(article.url)
        targets = WagtailPageTargetProvider().get_targets()

        self.assertEqual(response.status_code, 410)
        self.assertNotIn(
            "legacy-news/existing-legacy-article/index.html",
            {target.output_path for target in targets},
        )

    def test_migration_command_converts_and_unpublishes_legacy_article(self):
        journal = Journal.objects.create(
            name="Migration Journal",
            slug="migration-journal",
            az_group="M",
        )
        article = LegacyArticlePage(
            title="Migrated legacy article",
            slug="migrated-legacy-article",
            author=self.author,
            topic=self.topic,
            introduction="Old content to migrate.",
            body=[],
        )
        article._allow_legacy_article_save = True
        self.news_listing.add_child(instance=article)
        article.save_revision().publish()

        output = StringIO()
        call_command(
            "migrate_legacy_news_articles",
            "--journal-slug",
            journal.slug,
            stdout=output,
        )

        canonical = CanonicalArticlePage.objects.get(
            static_slug="migrated-legacy-article"
        )
        article.refresh_from_db()

        self.assertIn("created articles.ArticlePage", output.getvalue())
        self.assertEqual(canonical.primary_journal, journal)
        self.assertEqual(
            canonical.review_status, CanonicalArticlePage.ReviewStatus.APPROVED
        )
        self.assertFalse(article.live)
