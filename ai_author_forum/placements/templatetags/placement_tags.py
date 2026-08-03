from django import template

from ai_author_forum.placements.services import get_slot_items

register = template.Library()


@register.simple_tag
def slot_items(slot_code, target_type="main_site", target_slug="", limit=None):
    return get_slot_items(
        slot_code=slot_code,
        target_type=target_type or "main_site",
        target_slug=target_slug or "",
        limit=limit,
    )
