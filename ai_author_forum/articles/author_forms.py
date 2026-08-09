from __future__ import annotations

import json
import re
from uuid import uuid4

from django import forms
from django.contrib.auth import get_user_model
from django.utils.html import escape, strip_tags
from wagtail.whitelist import Whitelister

from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalCategoryStatus,
)
from ai_author_forum.journals.submission_services import public_submission_journals

from .models import ArticleAuthorship, ArticlePage

AUTHOR_BODY_BLOCK_TYPES = frozenset(
    {"paragraph", "heading", "image", "quote", "list", "table", "document"}
)
AUTHOR_BODY_BLOCK_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
AUTHOR_RICH_TEXT_LIMIT = 20_000
AUTHOR_TABLE_MAX_ROWS = 30
AUTHOR_TABLE_MAX_COLUMNS = 12
AUTHOR_TABLE_MAX_CELLS = 240


def _rich_text_from_plain_text(value):
    paragraphs = [
        item.strip() for item in str(value or "").split("\n\n") if item.strip()
    ]
    return "".join(
        f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs
    )


def clean_author_rich_text(value, *, label):
    """Restrict author formatting to Wagtail's controlled rich-text allowlist."""

    value = str(value or "").strip()
    if len(value) > AUTHOR_RICH_TEXT_LIMIT:
        raise forms.ValidationError(
            f"{label}不能超过 {AUTHOR_RICH_TEXT_LIMIT} 个字符。"
        )
    cleaned = Whitelister().clean(value)
    if not strip_tags(cleaned).strip():
        raise forms.ValidationError(f"{label}不能为空。")
    return cleaned


class JournalScopedCategorySelect(forms.Select):
    def __init__(self, *args, journal_source_id, **kwargs):
        attrs = dict(kwargs.pop("attrs", {}) or {})
        attrs["data-journal-source"] = journal_source_id
        super().__init__(*args, attrs=attrs, **kwargs)

    def create_option(self, name, value, label, selected, index, **kwargs):
        option = super().create_option(name, value, label, selected, index, **kwargs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-journal-id"] = str(instance.journal_id)
        return option


def plain_text_to_body(value):
    html = _rich_text_from_plain_text(value)
    return [("paragraph", html)] if html else []


def body_to_plain_text(body):
    values = []
    for block in body or []:
        if block.block_type == "paragraph":
            values.append(strip_tags(str(block.value)).strip())
        else:
            values.append(strip_tags(str(block.value)).strip())
    return "\n\n".join(item for item in values if item)


def body_to_author_blocks(body):
    """Expose only author-safe blocks when editing an existing draft.

    Imported historic Raw HTML is deliberately presented as controlled text. The
    author workbench never emits an ``html`` block or a media-library chooser.
    """

    if not body:
        return []
    raw_blocks = (
        body.get_prep_value() if hasattr(body, "get_prep_value") else list(body)
    )
    result = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        block_type = raw.get("type")
        value = raw.get("value")
        block_id = str(raw.get("id") or uuid4().hex)
        if block_type == "paragraph":
            try:
                html = clean_author_rich_text(value, label="正文段落")
            except forms.ValidationError:
                continue
            result.append({"id": block_id, "type": "paragraph", "html": html})
        elif block_type == "heading":
            result.append(
                {
                    "id": block_id,
                    "type": "heading",
                    "text": str(value or "")[:160],
                }
            )
        elif block_type == "image" and isinstance(value, dict):
            result.append(
                {
                    "id": block_id,
                    "type": "image",
                    "image_asset_id": value.get("image"),
                    "alt_text": str(value.get("alt_text") or "")[:255],
                    "caption": str(value.get("caption") or "")[:255],
                }
            )
        elif block_type == "quote" and isinstance(value, dict):
            result.append(
                {
                    "id": block_id,
                    "type": "quote",
                    "quote": str(value.get("quote") or ""),
                    "attribution": str(value.get("attribution") or "")[:255],
                }
            )
        elif block_type == "list" and isinstance(value, dict):
            items = []
            for item in value.get("items") or []:
                try:
                    items.append(clean_author_rich_text(item, label="列表项"))
                except forms.ValidationError:
                    continue
            if items:
                result.append(
                    {
                        "id": block_id,
                        "type": "list",
                        "list_type": value.get("list_type") or "unordered",
                        "items": items,
                    }
                )
        elif block_type == "table" and isinstance(value, dict):
            result.append(
                {
                    "id": block_id,
                    "type": "table",
                    "data": value.get("data") or [["", ""], ["", ""]],
                    "first_row_is_table_header": bool(
                        value.get("first_row_is_table_header")
                    ),
                    "first_col_is_header": bool(value.get("first_col_is_header")),
                    "caption": str(value.get("table_caption") or "")[:500],
                }
            )
        elif block_type == "document" and isinstance(value, dict):
            result.append(
                {
                    "id": block_id,
                    "type": "document",
                    "document_asset_id": value.get("document"),
                    "link_text": str(value.get("link_text") or "")[:160],
                    "description": str(value.get("description") or "")[:500],
                }
            )
        elif block_type == "html":
            text = strip_tags(str(value or "")).strip()
            if text:
                result.append(
                    {
                        "id": block_id,
                        "type": "paragraph",
                        "html": _rich_text_from_plain_text(text),
                    }
                )
    return result


class AuthorSubmissionFieldsForm(forms.Form):
    title = forms.CharField(max_length=255, label="标题")
    abstract = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), label="摘要")
    body_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput,
        label="正文",
        help_text="可插入正文段落、章节标题、图片、引用、列表、表格和附件。",
    )
    keywords = forms.CharField(max_length=255, label="关键词")
    responsibility_statement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        label="作者声明",
    )
    ai_co_authors = forms.CharField(max_length=255, required=False, label="AI 合著人")
    ai_contribution_statement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        label="AI 参与说明",
    )
    article_type = forms.ChoiceField(
        choices=ArticlePage.ArticleType.choices,
        label="文章类型",
    )
    category = forms.ModelChoiceField(
        queryset=JournalCategory.objects.none(),
        label="栏目",
        widget=JournalScopedCategorySelect(journal_source_id="id_journal"),
    )
    cover_image = forms.ImageField(
        required=False,
        label="封面图片",
        help_text="JPEG、PNG 或 WebP，最大 8 MiB。",
    )
    remove_cover = forms.BooleanField(required=False, label="移除当前封面")
    featured_image_alt = forms.CharField(
        max_length=255,
        required=False,
        label="封面替代文本",
    )
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_revision_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, journal=None, article=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal = journal or getattr(article, "primary_journal", None)
        if self.journal is not None:
            self.fields["category"].queryset = JournalCategory.objects.filter(
                journal=self.journal,
                status=JournalCategoryStatus.ACTIVE,
            ).order_by("path_cache", "sort_order", "pk")
        else:
            self.fields["category"].queryset = (
                JournalCategory.objects.filter(
                    journal_id__in=public_submission_journals().values("pk"),
                    status=JournalCategoryStatus.ACTIVE,
                )
                .select_related("journal")
                .order_by("journal__name", "path_cache", "pk")
            )
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid4())
            if article is not None:
                self.initial.update(
                    {
                        "title": article.title,
                        "abstract": article.abstract,
                        "body_json": json.dumps(
                            body_to_author_blocks(article.body), ensure_ascii=False
                        ),
                        "keywords": article.keywords,
                        "responsibility_statement": article.responsibility_statement,
                        "ai_co_authors": article.ai_co_authors,
                        "ai_contribution_statement": article.ai_contribution_statement,
                        "article_type": article.article_type,
                        "featured_image_alt": article.featured_image_alt,
                        "expected_revision_id": (
                            article.get_latest_revision().pk
                            if article.get_latest_revision()
                            else ""
                        ),
                    }
                )
                primary = article.category_assignments.filter(is_primary=True).first()
                if primary is not None:
                    self.initial["category"] = primary.category_id

    def clean_cover_image(self):
        upload = self.cleaned_data.get("cover_image")
        if upload is None:
            return None
        if upload.size > 8 * 1024 * 1024:
            raise forms.ValidationError("封面不能超过 8 MiB。")
        if upload.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise forms.ValidationError("封面仅支持 JPEG、PNG 或 WebP。")
        return upload

    def clean_body_json(self):
        raw_value = self.cleaned_data.get("body_json")
        # Keep existing form submissions working while the browser-side editor
        # rolls out. New workbench pages always submit ``body_json``.
        if not raw_value and self.data.get("body_text"):
            raw_value = json.dumps(
                [
                    {
                        "id": uuid4().hex,
                        "type": "paragraph",
                        "html": _rich_text_from_plain_text(self.data.get("body_text")),
                    }
                ],
                ensure_ascii=False,
            )
        if not raw_value:
            raise forms.ValidationError("请至少添加一个正文内容块。")
        if len(raw_value) > 250_000:
            raise forms.ValidationError("正文内容过大，请减少内容或拆分附件。")
        try:
            blocks = json.loads(raw_value)
        except (TypeError, ValueError) as exc:
            raise forms.ValidationError(
                "正文内容格式无效，请重新编辑后再保存。"
            ) from exc
        if not isinstance(blocks, list) or not blocks:
            raise forms.ValidationError("请至少添加一个正文内容块。")
        if len(blocks) > 100:
            raise forms.ValidationError("正文最多支持 100 个内容块。")

        cleaned_blocks = []
        seen_ids = set()
        for position, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                raise forms.ValidationError(f"第 {position} 个正文内容块格式无效。")
            block_id = str(block.get("id") or "")
            block_type = str(block.get("type") or "")
            if not AUTHOR_BODY_BLOCK_ID.fullmatch(block_id) or block_id in seen_ids:
                raise forms.ValidationError("正文内容块标识无效，请重新编辑后再保存。")
            if block_type not in AUTHOR_BODY_BLOCK_TYPES:
                raise forms.ValidationError("正文只支持受控内容块，不支持 Raw HTML。")
            seen_ids.add(block_id)
            cleaned_blocks.append(
                self._clean_author_body_block(
                    block_id=block_id,
                    block_type=block_type,
                    value=block,
                    position=position,
                )
            )
        return cleaned_blocks

    def _clean_author_body_block(self, *, block_id, block_type, value, position):
        if block_type == "paragraph":
            return {
                "id": block_id,
                "type": block_type,
                "html": clean_author_rich_text(
                    value.get("html"), label=f"第 {position} 个正文段落"
                ),
            }
        if block_type == "heading":
            text = str(value.get("text") or "").strip()
            if not text:
                raise forms.ValidationError(f"第 {position} 个章节标题不能为空。")
            if len(text) > 160:
                raise forms.ValidationError(
                    f"第 {position} 个章节标题不能超过 160 个字符。"
                )
            return {"id": block_id, "type": block_type, "text": text}
        if block_type == "image":
            asset_id = value.get("image_asset_id")
            try:
                asset_id = int(asset_id) if asset_id not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError(f"第 {position} 张图片无效。") from exc
            upload_key = f"body_image_{block_id}"
            if not asset_id and upload_key not in self.files:
                raise forms.ValidationError(f"第 {position} 张图片需要选择图片文件。")
            return {
                "id": block_id,
                "type": block_type,
                "image_asset_id": asset_id,
                "upload_key": upload_key if upload_key in self.files else "",
                "alt_text": str(value.get("alt_text") or "").strip()[:255],
                "caption": str(value.get("caption") or "").strip()[:255],
            }
        if block_type == "quote":
            quote = str(value.get("quote") or "").strip()
            if not quote:
                raise forms.ValidationError(f"第 {position} 个引用不能为空。")
            if len(quote) > 10_000:
                raise forms.ValidationError(
                    f"第 {position} 个引用不能超过 10000 个字符。"
                )
            return {
                "id": block_id,
                "type": block_type,
                "quote": quote,
                "attribution": str(value.get("attribution") or "").strip()[:255],
            }
        if block_type == "list":
            list_type = str(value.get("list_type") or "unordered")
            if list_type not in {"ordered", "unordered"}:
                raise forms.ValidationError(f"第 {position} 个列表类型无效。")
            items = value.get("items")
            if not isinstance(items, list) or not items or len(items) > 50:
                raise forms.ValidationError(
                    f"第 {position} 个列表需要 1 到 50 个列表项。"
                )
            return {
                "id": block_id,
                "type": block_type,
                "list_type": list_type,
                "items": [
                    clean_author_rich_text(item, label=f"第 {position} 个列表项")
                    for item in items
                ],
            }
        if block_type == "table":
            data = value.get("data")
            if (
                not isinstance(data, list)
                or not data
                or len(data) > AUTHOR_TABLE_MAX_ROWS
            ):
                raise forms.ValidationError(
                    f"第 {position} 个表格需要 1 到 {AUTHOR_TABLE_MAX_ROWS} 行。"
                )
            cleaned_rows = []
            column_count = None
            for row in data:
                if (
                    not isinstance(row, list)
                    or not row
                    or len(row) > AUTHOR_TABLE_MAX_COLUMNS
                ):
                    raise forms.ValidationError(
                        f"第 {position} 个表格每行需要 1 到 {AUTHOR_TABLE_MAX_COLUMNS} 列。"
                    )
                if column_count is None:
                    column_count = len(row)
                elif column_count != len(row):
                    raise forms.ValidationError(
                        f"第 {position} 个表格每行列数必须一致。"
                    )
                cleaned_row = []
                for cell in row:
                    cell = strip_tags(str(cell or "")).strip()
                    if len(cell) > 1_000:
                        raise forms.ValidationError("表格单元格不能超过 1000 个字符。")
                    cleaned_row.append(cell)
                cleaned_rows.append(cleaned_row)
            if len(cleaned_rows) * (column_count or 0) > AUTHOR_TABLE_MAX_CELLS:
                raise forms.ValidationError(
                    f"第 {position} 个表格不能超过 {AUTHOR_TABLE_MAX_CELLS} 个单元格。"
                )
            caption = str(value.get("caption") or "").strip()[:500]
            return {
                "id": block_id,
                "type": block_type,
                "data": cleaned_rows,
                "first_row_is_table_header": bool(
                    value.get("first_row_is_table_header")
                ),
                "first_col_is_header": bool(value.get("first_col_is_header")),
                "caption": caption,
            }
        if block_type == "document":
            asset_id = value.get("document_asset_id")
            try:
                asset_id = int(asset_id) if asset_id not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError(f"第 {position} 个附件无效。") from exc
            upload_key = f"body_document_{block_id}"
            if not asset_id and upload_key not in self.files:
                raise forms.ValidationError(f"第 {position} 个附件需要选择文件。")
            return {
                "id": block_id,
                "type": block_type,
                "document_asset_id": asset_id,
                "upload_key": upload_key if upload_key in self.files else "",
                "link_text": str(value.get("link_text") or "").strip()[:160],
                "description": str(value.get("description") or "").strip()[:500],
            }
        raise forms.ValidationError("正文内容块格式无效。")

    def body_uploads(self):
        blocks = self.cleaned_data.get("body_json") or []
        keys = {block.get("upload_key") for block in blocks if block.get("upload_key")}
        return {key: self.files[key] for key in keys if key in self.files}

    def service_fields(self):
        return {
            "title": self.cleaned_data["title"].strip(),
            "abstract": self.cleaned_data["abstract"].strip(),
            "body": self.cleaned_data["body_json"],
            "keywords": self.cleaned_data["keywords"].strip(),
            "responsibility_statement": self.cleaned_data[
                "responsibility_statement"
            ].strip(),
            "ai_co_authors": self.cleaned_data["ai_co_authors"].strip(),
            "ai_contribution_statement": self.cleaned_data[
                "ai_contribution_statement"
            ].strip(),
            "article_type": self.cleaned_data["article_type"],
            "featured_image_alt": self.cleaned_data["featured_image_alt"].strip(),
        }


class AuthorSubmissionCreateForm(AuthorSubmissionFieldsForm):
    journal = forms.ModelChoiceField(
        queryset=Journal.objects.none(),
        label="主投期刊",
    )

    def __init__(self, *args, **kwargs):
        journal = kwargs.pop("journal", None)
        if journal is None and args and hasattr(args[0], "get"):
            try:
                journal = Journal.objects.filter(pk=args[0].get("journal")).first()
            except (TypeError, ValueError):
                journal = None
        super().__init__(*args, journal=journal, **kwargs)
        self.fields["journal"].queryset = public_submission_journals()
        if not self.is_bound and journal is not None:
            self.initial["journal"] = journal.pk

    def clean(self):
        cleaned = super().clean()
        journal = cleaned.get("journal")
        category = cleaned.get("category")
        if journal and category and category.journal_id != journal.pk:
            self.add_error("category", "所选栏目不属于主投期刊。")
        return cleaned


class AuthorContributorForm(forms.Form):
    name = forms.CharField(max_length=255, label="姓名")
    affiliation = forms.CharField(max_length=500, required=False, label="单位")
    is_corresponding = forms.BooleanField(required=False, label="通讯作者")


class BaseAuthorContributorFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if not active:
            raise forms.ValidationError("至少需要一名公开作者。")
        if sum(1 for row in active if row.get("is_corresponding")) != 1:
            raise forms.ValidationError("必须且只能指定一名通讯作者。")

    def service_rows(self):
        return [
            {
                "name": form.cleaned_data["name"],
                "affiliation": form.cleaned_data.get("affiliation", ""),
                "is_corresponding": form.cleaned_data.get("is_corresponding", False),
            }
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]


AuthorContributorFormSet = forms.formset_factory(
    AuthorContributorForm,
    formset=BaseAuthorContributorFormSet,
    extra=1,
    min_num=1,
    max_num=20,
    validate_min=True,
    validate_max=True,
    can_delete=True,
)


def contributor_initial(article=None, user=None):
    if article is not None:
        rows = [
            {
                "name": item.name,
                "affiliation": item.affiliation,
                "is_corresponding": item.is_corresponding,
            }
            for item in article.contributors.filter(
                identity=article.contributors.model.Identity.AUTHOR
            ).order_by("sort_order", "pk")
        ]
        if rows:
            return rows
    return [
        {
            "name": getattr(user, "display_name", ""),
            "affiliation": getattr(user, "institution", ""),
            "is_corresponding": True,
        }
    ]


class ChangeSubmissionJournalForm(forms.Form):
    target_journal = forms.ModelChoiceField(
        queryset=Journal.objects.none(), label="目标期刊"
    )
    target_category = forms.ModelChoiceField(
        queryset=JournalCategory.objects.none(),
        label="目标栏目",
        widget=JournalScopedCategorySelect(journal_source_id="id_target_journal"),
    )
    expected_revision_id = forms.IntegerField(widget=forms.HiddenInput)
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label="确认更换主投期刊并清除原期刊分类")

    def __init__(self, *args, current_journal=None, **kwargs):
        super().__init__(*args, **kwargs)
        journal_queryset = public_submission_journals()
        if current_journal is not None:
            journal_queryset = journal_queryset.exclude(pk=current_journal.pk)
        self.fields["target_journal"].queryset = journal_queryset
        journal_id = self.data.get("target_journal") if self.is_bound else None
        if journal_id:
            self.fields["target_category"].queryset = JournalCategory.objects.filter(
                journal_id=journal_id, status=JournalCategoryStatus.ACTIVE
            ).order_by("path_cache", "pk")
        else:
            self.fields["target_category"].queryset = (
                JournalCategory.objects.filter(
                    journal_id__in=public_submission_journals().values("pk"),
                    status=JournalCategoryStatus.ACTIVE,
                )
                .select_related("journal")
                .order_by("journal__name", "path_cache", "pk")
            )
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid4())


class SubmitAuthorSubmissionForm(forms.Form):
    expected_revision_id = forms.IntegerField(widget=forms.HiddenInput)
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    comment = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="给编辑的提交说明",
    )
    confirmed = forms.BooleanField(label="我确认提交后内容将锁定并进入初审")


class AuthorshipGrantForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(), label="作者账号"
    )
    role = forms.ChoiceField(choices=ArticleAuthorship.Role.choices, label="投稿角色")
    can_edit = forms.BooleanField(required=False, label="允许编辑")
    is_corresponding = forms.BooleanField(required=False, label="通讯作者")
    request_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = (
            get_user_model()
            .objects.filter(
                is_author=True,
                is_active=True,
                account_status="active",
            )
            .order_by("display_name", "username")
        )
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid4())


class AuthorshipRevokeForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="撤销原因")
    request_id = forms.UUIDField(widget=forms.HiddenInput)


class ControlledTransferForm(forms.Form):
    target_journal = forms.ModelChoiceField(
        queryset=Journal.objects.none(), label="目标期刊"
    )
    target_category = forms.ModelChoiceField(
        queryset=JournalCategory.objects.none(),
        label="目标栏目",
        widget=JournalScopedCategorySelect(journal_source_id="id_target_journal"),
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="转投原因")
    expected_state = forms.CharField(widget=forms.HiddenInput)
    expected_revision_id = forms.IntegerField(widget=forms.HiddenInput)
    request_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, current_journal=None, **kwargs):
        super().__init__(*args, **kwargs)
        journal_queryset = public_submission_journals()
        if current_journal is not None:
            journal_queryset = journal_queryset.exclude(pk=current_journal.pk)
        self.fields["target_journal"].queryset = journal_queryset
        journal_id = self.data.get("target_journal") if self.is_bound else None
        if journal_id:
            self.fields["target_category"].queryset = JournalCategory.objects.filter(
                journal_id=journal_id, status=JournalCategoryStatus.ACTIVE
            ).order_by("path_cache", "pk")
        else:
            self.fields["target_category"].queryset = (
                JournalCategory.objects.filter(
                    journal_id__in=public_submission_journals().values("pk"),
                    status=JournalCategoryStatus.ACTIVE,
                )
                .select_related("journal")
                .order_by("journal__name", "path_cache", "pk")
            )
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid4())
