from collections import Counter

from django.core.exceptions import ValidationError

from ai_author_forum.site_settings.access_control import is_super_admin

RAW_HTML_PERMISSION = "articles.use_raw_html"


def user_can_use_raw_html(user):
    return bool(user and user.is_active and is_super_admin(user))


def body_contains_raw_html(value):
    return bool(extract_raw_html_fragments(value))


def extract_raw_html_fragments(value):
    raw_data = getattr(value, "raw_data", value)
    return tuple(_iter_raw_html_fragments(raw_data))


def _iter_raw_html_fragments(value):
    if isinstance(value, dict):
        if value.get("type") == "html":
            yield str(value.get("value", ""))
            return
        for item in value.values():
            yield from _iter_raw_html_fragments(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _iter_raw_html_fragments(item)


def validate_raw_html_permission(*, user, body, original_body=None):
    if user_can_use_raw_html(user):
        return

    submitted_fragments = extract_raw_html_fragments(body)
    if not submitted_fragments:
        return

    original_fragments = Counter(extract_raw_html_fragments(original_body))
    for fragment in submitted_fragments:
        if original_fragments[fragment] <= 0:
            raise ValidationError(
                "您没有使用 Raw HTML 正文块的权限；请删除该块或联系管理员授权。",
                code="raw_html_permission_required",
            )
        original_fragments[fragment] -= 1
