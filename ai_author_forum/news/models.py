from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import models
from django.http import HttpResponseGone, HttpResponsePermanentRedirect
from django.utils.text import slugify
from wagtail.admin.panels import FieldPanel, HelpPanel, InlinePanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page
from wagtail.search import index

from ai_author_forum.utils.blocks import CaptionedImageBlock, StoryBlock
from ai_author_forum.utils.models import BasePage


class ArticlePage(BasePage):
    is_creatable = False
    page_ptr = models.OneToOneField(
        Page,
        on_delete=models.CASCADE,
        parent_link=True,
        related_name="news_articlepage",
    )
    template = "pages/article_page.html"
    parent_page_types = []

    author = models.ForeignKey(
        "utils.AuthorSnippet",
        blank=False,
        null=False,
        on_delete=models.deletion.PROTECT,
        related_name="+",
    )
    topic = models.ForeignKey(
        "utils.ArticleTopic",
        blank=False,
        null=False,
        on_delete=models.deletion.PROTECT,
        related_name="article_pages",
    )
    publication_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Use this field to override the date that the "
        "news item appears to have been published.",
    )
    introduction = models.TextField(blank=True)
    image = StreamField(
        [("image", CaptionedImageBlock())],
        blank=True,
        max_num=1,
    )
    body = StreamField(StoryBlock())
    featured_section_title = models.TextField(blank=True)

    search_fields = BasePage.search_fields + [
        index.SearchField("introduction"),
        index.FilterField("topic"),
    ]

    content_panels = BasePage.content_panels + [
        FieldPanel("author"),
        FieldPanel("publication_date"),
        FieldPanel("topic"),
        FieldPanel("introduction"),
        FieldPanel("image"),
        FieldPanel("body"),
        MultiFieldPanel(
            [
                FieldPanel("featured_section_title", heading="Title"),
                InlinePanel(
                    "page_related_pages",
                    label="Pages",
                    max_num=3,
                ),
            ],
            heading="Featured section",
        ),
    ]

    @property
    def display_date(self):
        if self.publication_date:
            return self.publication_date.strftime("%d %b %Y")
        elif self.first_published_at:
            return self.first_published_at.strftime("%d %b %Y")

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(
            self,
            "_allow_legacy_article_save",
            False,
        ):
            raise ValidationError(
                "Legacy news.ArticlePage is retired. Create articles.ArticlePage "
                "records so moderation and placement rules are enforced."
            )
        return super().save(*args, **kwargs)

    def serve(self, request, *args, **kwargs):
        canonical_url = self._get_canonical_redirect_url()
        if canonical_url:
            return HttpResponsePermanentRedirect(canonical_url)
        return HttpResponseGone(
            "This legacy article URL has been retired. Use the canonical "
            "articles workflow."
        )

    def _get_canonical_redirect_url(self):
        from ai_author_forum.articles.models import ArticlePage as CanonicalArticlePage
        from ai_author_forum.articles.services import get_article_context

        canonical = (
            CanonicalArticlePage.objects.filter(static_slug=self.slug).first()
            or CanonicalArticlePage.objects.filter(slug=self.slug).first()
        )
        if canonical is None:
            return ""

        try:
            get_article_context(canonical.static_slug)
        except CanonicalArticlePage.DoesNotExist:
            return ""
        return canonical.get_absolute_url()


class NewsListingPage(BasePage):
    template = "pages/news_listing_page.html"
    subpage_types = []
    max_count = 1  # Allow only one news listing page to keep article pages in one place

    introduction = RichTextField(blank=True, features=["bold", "italic", "link"])

    search_fields = BasePage.search_fields + [index.SearchField("introduction")]

    content_panels = BasePage.content_panels + [
        FieldPanel("introduction"),
        # FieldPanel("featured_card"),
        HelpPanel("This page will automatically display child Article pages."),
    ]

    def paginate_queryset(self, queryset, request):
        """Paginate the queryset."""
        page_number = request.GET.get("page", 1)
        paginator = Paginator(queryset, settings.DEFAULT_PER_PAGE)
        try:
            page = paginator.page(page_number)
        except PageNotAnInteger:
            page = paginator.page(1)
        except EmptyPage:
            page = paginator.page(paginator.num_pages)
        return (paginator, page, page.object_list, page.has_other_pages())

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        from ai_author_forum.articles.models import ArticlePage as CanonicalArticlePage
        from ai_author_forum.articles.services import get_approved_articles

        queryset = get_approved_articles()
        article_topics = [
            {
                "title": label,
                "slug": slugify(value, allow_unicode=True),
                "value": value,
            }
            for value, label in CanonicalArticlePage.ArticleType.choices
        ]
        matching_topic = False

        topic_query_param = request.GET.get("topic")
        topic_values = {topic["slug"]: topic["value"] for topic in article_topics}
        if topic_query_param and topic_query_param in topic_values:
            matching_topic = topic_query_param
            queryset = queryset.filter(article_type=topic_values[topic_query_param])

        # Paginate article pages
        paginator, page, _object_list, is_paginated = self.paginate_queryset(
            queryset, request
        )
        context["paginator"] = paginator
        context["paginator_page"] = page
        context["is_paginated"] = is_paginated

        # Topics
        context["topics"] = article_topics
        context["matching_topic"] = matching_topic
        context["canonical_article_listing"] = True

        return context
