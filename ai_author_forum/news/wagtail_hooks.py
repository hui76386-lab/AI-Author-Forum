from django.contrib import messages
from django.core.exceptions import ValidationError
from wagtail import hooks
from wagtail.admin.auth import permission_denied

from ai_author_forum.news.models import ArticlePage as LegacyArticlePage

LEGACY_ARTICLE_MESSAGE = (
    "Legacy news.ArticlePage has been retired. Use articles.ArticlePage so "
    "moderation and placement rules are enforced."
)


@hooks.register("before_create_page")
def block_legacy_news_article_create(request, parent_page, page_class):
    if page_class is LegacyArticlePage:
        messages.error(request, LEGACY_ARTICLE_MESSAGE)
        return permission_denied(request)
    return None


@hooks.register("before_publish_page")
def block_legacy_news_article_publish(request, page):
    if isinstance(page.specific, LegacyArticlePage):
        messages.error(request, LEGACY_ARTICLE_MESSAGE)
        return permission_denied(request)
    return None


@hooks.register("before_submit_page")
def block_legacy_news_article_submit(request, page):
    if isinstance(page.specific, LegacyArticlePage):
        messages.error(request, LEGACY_ARTICLE_MESSAGE)
        return permission_denied(request)
    return None


def validate_legacy_news_article_retired():
    raise ValidationError(LEGACY_ARTICLE_MESSAGE)
