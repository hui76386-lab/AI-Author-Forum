from django import template

from ai_author_forum.utils.admin_ui import admin_english

register = template.Library()


@register.filter
def admin_en(value):
    return admin_english(value)
