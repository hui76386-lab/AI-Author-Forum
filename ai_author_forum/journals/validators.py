from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

_ALLOWED_RICH_TEXT_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "em",
    "h2",
    "h3",
    "i",
    "li",
    "ol",
    "p",
    "strong",
    "ul",
}
_ALLOWED_RICH_TEXT_ATTRIBUTES = {
    "p": {"data-block-key"},
    "h2": {"data-block-key"},
    "h3": {"data-block-key"},
    "blockquote": {"data-block-key"},
    "li": {"data-block-key"},
    "a": {"href", "id", "linktype", "rel", "target"},
}
_DANGEROUS_RICH_TEXT_SCHEMES = {"data", "file", "javascript", "vbscript"}
_REPEATED_QUESTION_MARKS = re.compile(r"\?{3,}")
_MOJIBAKE_MARKERS = (
    "Ã",
    "Â",
    "â€™",
    "â€œ",
    "â€",
    "ðŸ",
    "锟斤拷",
    "鏂囧瓧",
    "娴嬭瘯",
    "浜哄伐",
    "æ–‡",
    "ä¸­",
)


@dataclass(frozen=True)
class SuspiciousTextIssue:
    field_name: str
    rule: str
    severity: str
    message: str
    raw_value: str
    suggestion: str
    blocking: bool = True

    def as_dict(self) -> dict:
        data = asdict(self)
        data["raw_value"] = truncate_text(self.raw_value)
        return data


def truncate_text(value: object, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def detect_suspicious_text(
    value: object, *, field_name: str = ""
) -> list[SuspiciousTextIssue]:
    """Detect corrupted-looking text without attempting any irreversible conversion."""
    if value is None or isinstance(value, bool | int | float):
        return []
    text = str(value)
    if not text:
        return []
    issues: list[SuspiciousTextIssue] = []

    def add(rule, severity, message, suggestion):
        issues.append(
            SuspiciousTextIssue(
                field_name=field_name,
                rule=rule,
                severity=severity,
                message=message,
                raw_value=text,
                suggestion=suggestion,
            )
        )

    if "\ufffd" in text:
        add(
            "unicode_replacement_character",
            "error",
            "包含 Unicode 替换字符“�”，原始字节可能已错误解码。",
            "请使用可信原始文件重新导入，或提供人工确认映射。",
        )
    if _REPEATED_QUESTION_MARKS.search(text):
        add(
            "repeated_question_marks",
            "error",
            "包含三个或更多连续问号，疑似源数据已丢失字符。",
            "请核对原始数据；不要根据上下文猜测恢复。",
        )
    illegal_controls = sorted(
        {
            f"U+{ord(char):04X}"
            for char in text
            if unicodedata.category(char) == "Cc" and char not in "\t\n\r"
        }
    )
    if illegal_controls:
        add(
            "illegal_control_character",
            "error",
            f"包含非法控制字符：{', '.join(illegal_controls)}。",
            "请在可信源文件中删除控制字符后重新导入。",
        )
    markers = [marker for marker in _MOJIBAKE_MARKERS if marker in text]
    if markers:
        add(
            "possible_mojibake",
            "warning",
            f"检测到常见错解码特征：{', '.join(markers[:3])}。",
            "请核对文件编码；系统只告警，不会自动转换文本。",
        )
    return issues


def scan_mapping_for_suspicious_text(row: dict) -> list[dict]:
    issues: list[dict] = []
    for field_name, value in row.items():
        if str(field_name).startswith("_"):
            continue
        for issue in detect_suspicious_text(value, field_name=str(field_name)):
            issues.append(issue.as_dict())
    return issues


class _ControlledRichTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._check_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._check_tag(tag, attrs)

    def _check_tag(self, tag, attrs):
        tag = tag.lower()
        if tag not in _ALLOWED_RICH_TEXT_TAGS:
            self.errors.append(f"tag <{tag}> is not allowed")
            return
        allowed_attrs = _ALLOWED_RICH_TEXT_ATTRIBUTES.get(tag, set())
        for name, value in attrs:
            name = name.lower()
            if name not in allowed_attrs or name.startswith("on"):
                self.errors.append(f"attribute {name!r} is not allowed")
                continue
            if name in {"href", "src", "action"} and value:
                scheme = urlsplit(value.strip()).scheme.lower()
                if scheme in _DANGEROUS_RICH_TEXT_SCHEMES:
                    self.errors.append(f"URL scheme {scheme!r} is not allowed")


def validate_controlled_rich_text(value):
    """Reject markup that cannot be represented by the controlled Hero editor."""
    if not value:
        return
    parser = _ControlledRichTextParser()
    try:
        parser.feed(str(value))
        parser.close()
    except Exception as exc:  # pragma: no cover
        raise ValidationError("首页简介包含无法解析的富文本。") from exc
    if parser.errors:
        raise ValidationError("首页简介仅允许加粗、斜体、超链接、无序列表和有序列表。")


def validate_public_link(value):
    """Allow relative site links and validated HTTP(S) links only."""
    if value is None:
        return
    value = str(value).strip()
    if not value:
        return
    if value.startswith(("/", "#", "?")) and not value.startswith("//"):
        return
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("链接必须是站内路径或完整的 HTTP(S) 地址。")
    URLValidator(schemes=["http", "https"])(value)
