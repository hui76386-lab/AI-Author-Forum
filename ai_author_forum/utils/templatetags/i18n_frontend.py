from django import template
from django.utils import translation

from ai_author_forum.utils.i18n import (
    article_type_label as localized_article_type_label,
    localize_path,
    localized_journal_name as get_localized_journal_name,
    normalize_language,
    ui_label,
)

register = template.Library()


@register.simple_tag(takes_context=True)
def ui_text(context, key, default="", **variables):
    label = ui_label(key, translation.get_language(), default or key)
    if not variables:
        return label
    try:
        return label.format(**variables)
    except (KeyError, ValueError):
        return label


@register.filter
def article_type_label(value):
    return localized_article_type_label(value)


@register.filter
def localized_journal_name(value):
    return get_localized_journal_name(value)


@register.filter
def localized_url(url, language_code=None):
    return localize_path(url, normalize_language(language_code))


@register.simple_tag(takes_context=True)
def localize_url(context, url, language_code=None):
    return localize_path(url, normalize_language(language_code))
