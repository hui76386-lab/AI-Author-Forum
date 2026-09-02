from datetime import datetime
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Page, Site

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)


class LatestArticlesFeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.site = Site.objects.get(is_default_site=True)
        cls.site.hostname = "testserver"
        cls.site.site_name = "Test News"
        cls.site.save()

        cls.home = HomePage.objects.first()
        cls.news_listing = NewsListingPage(
            title="News",
            slug="news",
            introduction="Latest updates from the newsroom.",
            search_description="News feed description",
        )
        cls.home.add_child(instance=cls.news_listing)
        cls.news_listing.save_revision().publish()

        cls.journal = Journal.objects.create(
            name="Feed Journal",
            slug="feed-journal",
            az_group="F",
        )
        cls.slot = LayoutSlot.objects.get(code="section_article_list")
        cls.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="feed-admin",
                email="feed-admin@example.com",
                display_name="Feed Admin",
                password="test-password",
                is_staff=True,
            )
        )

        cls.published_article = cls.create_article(
            "Published RSS article",
            "published-rss-article",
            status=ArticlePage.ReviewStatus.APPROVED,
        )
        ArticlePlacement.objects.create(
            article=cls.published_article,
            slot=cls.slot,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
        )

        cls.unplaced_article = cls.create_article(
            "Unplaced RSS article",
            "unplaced-rss-article",
            status=ArticlePage.ReviewStatus.APPROVED,
        )
        cls.draft_article = cls.create_article(
            "Draft RSS article",
            "draft-rss-article",
            status=ArticlePage.ReviewStatus.DRAFT,
        )
        cls.feed_url = reverse("news_feed")

    @classmethod
    def create_article(cls, title, slug, status):
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract=f"{title} summary.",
            body=[("paragraph", f"<p>{title} body.</p>")],
            authors="Example Author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=cls.journal,
            keywords="rss, canonical",
        )
        (HomePage.objects.first() or Page.get_first_root_node()).add_child(
            instance=article
        )
        article.save_revision().publish()
        if status == ArticlePage.ReviewStatus.APPROVED:
            formally_approve_test_article(article, actor=cls.admin)
        ArticlePage.objects.filter(pk=article.pk).update(
            first_published_at=timezone.make_aware(datetime(2024, 1, 2, 9, 30))
        )
        return ArticlePage.objects.get(pk=article.pk)

    def _feed_xml(self):
        response = self.client.get(self.feed_url)
        return response, ElementTree.fromstring(response.content)

    def test_feed_is_accessible(self):
        response = self.client.get(self.feed_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/rss+xml", response["Content-Type"])

    def test_feed_returns_rss_2(self):
        _response, root = self._feed_xml()
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.attrib["version"], "2.0")

    def test_feed_title_includes_site_and_listing_titles(self):
        _response, root = self._feed_xml()
        self.assertEqual(root.findtext("channel/title"), "Test News - News")

    def test_feed_includes_only_placed_approved_canonical_articles(self):
        _response, root = self._feed_xml()
        channel = root.find("channel")
        item_titles = [item.findtext("title") for item in channel.findall("item")]
        self.assertIn("Published RSS article", item_titles)
        self.assertNotIn("Unplaced RSS article", item_titles)
        self.assertNotIn("Draft RSS article", item_titles)

    def test_feed_item_metadata(self):
        _response, root = self._feed_xml()
        item = root.find("channel/item")
        self.assertEqual(item.findtext("title"), "Published RSS article")
        self.assertEqual(
            item.findtext("description"),
            "Published RSS article summary.",
        )
        namespaces = {"dc": "http://purl.org/dc/elements/1.1/"}
        self.assertEqual(
            item.findtext("dc:creator", namespaces=namespaces), "Example Author"
        )
        categories = [node.text for node in item.findall("category")]
        self.assertIn("News", categories)
        self.assertIn("Feed Journal", categories)
        self.assertIn("/articles/published-rss-article/", item.findtext("link"))
        self.assertIn("/articles/published-rss-article/", item.findtext("guid"))
        self.assertIsNotNone(item.findtext("pubDate"))
