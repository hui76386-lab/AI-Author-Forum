from django.contrib.syndication.views import Feed
from django.urls import reverse
from wagtail.models import Site

from ai_author_forum.articles.services import get_approved_articles
from ai_author_forum.news.models import NewsListingPage


class LatestArticlesFeed(Feed):
    """RSS 2.0 feed for public canonical articles."""

    @property
    def site(self):
        return Site.objects.filter(is_default_site=True).first()

    @property
    def news_listing(self):
        return NewsListingPage.objects.live().public().first()

    def get_object(self, request):
        self.request = request

    def title(self, obj):
        parts = []
        if self.site and self.site.site_name:
            parts.append(self.site.site_name)
        if self.news_listing and self.news_listing.title:
            parts.append(self.news_listing.title)
        return " - ".join(parts) or "News"

    def description(self, obj):
        if self.news_listing:
            return (
                self.news_listing.search_description
                or self.news_listing.listing_summary
                or self.news_listing.plain_introduction
                or f"Latest articles from {self.title(obj)}"
            )
        return f"Latest articles from {self.title(obj)}"

    def link(self, obj):
        if self.news_listing:
            return self.news_listing.get_full_url(self.request)
        return self.request.build_absolute_uri(reverse("news_feed"))

    def feed_url(self, obj):
        return self.request.build_absolute_uri(reverse("news_feed"))

    def items(self, obj):
        return get_approved_articles()

    def item_title(self, item):
        return item.title

    def item_link(self, item):
        return self.request.build_absolute_uri(item.get_absolute_url())

    def item_description(self, item):
        return item.abstract or item.search_description

    def item_pubdate(self, item):
        return item.first_published_at or item.latest_revision_created_at

    def item_guid(self, item):
        return self.item_link(item)

    def item_author_name(self, item):
        return item.authors

    def item_categories(self, item):
        categories = [item.article_type]
        if item.primary_journal_id:
            categories.append(str(item.primary_journal))
        return categories
