from django import template
from django.utils import translation

from ai_author_forum.utils.i18n import (
    article_type_label as localized_article_type_label,
    localize_path,
    localized_journal_name as get_localized_journal_name,
    normalize_language,
    ui_label,
)
from ai_author_forum.utils.public_i18n import (
    localized_article_abstract as get_localized_article_abstract,
    localized_article_authors as get_localized_article_authors,
    localized_article_keywords as get_localized_article_keywords,
    localized_article_title as get_localized_article_title,
    localized_category_description as get_localized_category_description,
    localized_category_name as get_localized_category_name,
    localized_discipline_name as get_localized_discipline_name,
    localized_journal_action_label as get_localized_journal_action_label,
    localized_page_title as get_localized_page_title,
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
def localized_article_title(value):
    return get_localized_article_title(value)


@register.filter
def localized_article_abstract(value):
    return get_localized_article_abstract(value)


@register.filter
def localized_article_authors(value):
    return get_localized_article_authors(value)


@register.filter
def localized_article_contributor_identity(value):
    if not value:
        return ""
    return value.display_identity(translation.get_language())


@register.filter
def localized_article_keywords(value):
    return get_localized_article_keywords(value)


@register.filter
def localized_category_name(value):
    return get_localized_category_name(value)


@register.filter
def localized_category_description(value):
    return get_localized_category_description(value)


@register.filter
def localized_discipline_name(value):
    return get_localized_discipline_name(value)


@register.filter
def localized_page_title(value):
    return get_localized_page_title(value)


@register.filter
def localized_journal_action_label(value, url=""):
    return get_localized_journal_action_label(value, url)


@register.filter
def localized_url(url, language_code=None):
    return localize_path(url, normalize_language(language_code))


@register.simple_tag(takes_context=True)
def localize_url(context, url, language_code=None):
    return localize_path(url, normalize_language(language_code))


@register.simple_tag
def article_type_text(value, default=""):
    """Render article types from persisted data in the active public language."""
    normalized = str(value or "").strip().lower()
    key = {
        "ai article": "ai_article",
        "ai_article": "ai_article",
        "news": "news",
        "opinion": "opinion",
        "research analysis": "research_analysis",
        "research_analysis": "research_analysis",
    }.get(normalized)
    return (
        ui_label(key, translation.get_language(), default or value)
        if key
        else (default or value)
    )


@register.simple_tag
def journal_display_name(journal):
    """Use the journal's English name for /en/ without changing stored content."""
    if normalize_language(translation.get_language()) == "en":
        return journal.name or journal.name_cn or journal.slug
    return journal.name_cn or journal.name or journal.slug


@register.filter
def localize_journal_rich_text(value, journal):
    """Replace the Chinese journal name inside imported rich text on English pages."""
    source = str(value or "")
    if (
        normalize_language(translation.get_language()) == "en"
        and journal.name_cn
        and journal.name
    ):
        return source.replace(journal.name_cn, journal.name)
    return source
