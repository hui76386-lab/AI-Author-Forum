from django import template

from ai_author_forum.articles.display import resolve_article_image

register = template.Library()


@register.simple_tag(takes_context=True)
def article_image(context, article, placement=None):
    request = context.get("request")
    site_settings = context.get("site_settings")
    return resolve_article_image(
        article,
        placement=placement,
        request=request,
        site_settings=site_settings,
    )
