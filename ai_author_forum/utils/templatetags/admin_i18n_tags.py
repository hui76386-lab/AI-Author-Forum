from html import escape

from django import template
from django.utils.safestring import mark_safe

from ai_author_forum.utils.admin_ui import admin_english

register = template.Library()


@register.filter
def admin_en(value):
    return admin_english(value)


@register.filter
def admin_data(value):
    """Protect operator-visible business data from legacy response translation."""
    escaped = escape(str(value or ""), quote=True)
    return mark_safe(escaped.encode("ascii", "xmlcharrefreplace").decode("ascii"))
