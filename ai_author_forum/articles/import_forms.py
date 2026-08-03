from __future__ import annotations

from pathlib import Path

from django import forms
from django.conf import settings

from ai_author_forum.journals.models import ArticleType, Journal, JournalStatus

ALLOWED_ARTICLE_IMPORT_SUFFIXES = {".xlsx", ".csv", ".zip", ".docx", ".md", ".markdown"}
DOCUMENT_IMPORT_SUFFIXES = {".docx", ".md", ".markdown"}
MAX_ARTICLE_IMPORT_FILE_SIZE = 50 * 1024 * 1024


class ArticleImportUploadForm(forms.Form):
    source_file = forms.FileField(
        label="文章导入文件",
        help_text=(
            "支持 XLSX、CSV、ZIP、DOCX、MD 和 Markdown；上传后先预检，"
            "人工确认后仅写入文章草稿。"
        ),
        widget=forms.FileInput(attrs={"accept": ".xlsx,.csv,.zip,.docx,.md,.markdown"}),
    )
    default_journal = forms.ModelChoiceField(
        label="默认子期刊",
        queryset=Journal.objects.none(),
        required=False,
        help_text="全局模式可选。未填写 journal_slug 的行将使用这里选择的子期刊。",
    )
    document_title = forms.CharField(label="文档标题", max_length=255, required=False)
    document_slug = forms.SlugField(label="文档 slug", max_length=255, required=False)
    document_article_type = forms.ChoiceField(
        label="文章类型", choices=ArticleType.choices, required=False
    )
    document_authors = forms.CharField(label="作者", max_length=500, required=False)
    document_ai_co_authors = forms.CharField(
        label="AI 共同作者", max_length=500, required=False
    )
    document_publication_date = forms.DateField(
        label="内容发布日期", required=False, input_formats=["%Y-%m-%d"]
    )
    csv_encoding = forms.ChoiceField(
        label="CSV 编码",
        choices=(("auto", "UTF-8 / UTF-8-SIG"), ("gb18030", "GB18030")),
        initial="auto",
        required=False,
    )

    def __init__(self, *args, target_journal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_journal = target_journal
        self.fields["default_journal"].queryset = Journal.objects.filter(
            status=JournalStatus.ACTIVE
        ).order_by("sort_order", "name", "pk")
        if target_journal is not None:
            self.fields["default_journal"].disabled = True
            self.fields["default_journal"].initial = target_journal
            self.fields["default_journal"].required = False
            self.fields["default_journal"].help_text = (
                "本刊模式由服务端锁定，不能在上传页切换。"
            )

    def clean_source_file(self):
        source_file = self.cleaned_data["source_file"]
        suffix = Path(source_file.name or "").suffix.lower()
        if suffix not in ALLOWED_ARTICLE_IMPORT_SUFFIXES:
            raise forms.ValidationError(
                "仅支持 XLSX、CSV、ZIP、DOCX、MD 或 Markdown 文件。"
            )
        if suffix in DOCUMENT_IMPORT_SUFFIXES and not getattr(
            settings, "ARTICLE_DOCUMENT_IMPORT_ENABLED", True
        ):
            raise forms.ValidationError("DOCX/Markdown 文档导入功能当前已关闭。")
        if source_file.size > MAX_ARTICLE_IMPORT_FILE_SIZE:
            raise forms.ValidationError("导入文件不能超过 50 MB。")

        content_type = (getattr(source_file, "content_type", "") or "").lower()
        expected_content_types = {
            ".docx": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/octet-stream",
                "application/zip",
            },
            ".md": {"text/markdown", "text/plain", "application/octet-stream"},
            ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
            ".xlsx": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
                "application/zip",
            },
            ".csv": {
                "text/csv",
                "application/csv",
                "text/plain",
                "application/vnd.ms-excel",
                "application/octet-stream",
            },
            ".zip": {
                "application/zip",
                "application/x-zip-compressed",
                "application/octet-stream",
            },
        }
        if content_type and content_type not in expected_content_types[suffix]:
            raise forms.ValidationError("文件扩展名与上传内容类型不一致。")
        return source_file

    def clean(self):
        cleaned_data = super().clean()
        if self.target_journal is not None:
            cleaned_data["default_journal"] = None
        source_file = cleaned_data.get("source_file")
        suffix = Path(getattr(source_file, "name", "")).suffix.lower()
        if (
            source_file is not None
            and suffix in DOCUMENT_IMPORT_SUFFIXES
            and self.target_journal is None
            and not cleaned_data.get("default_journal")
        ):
            self.add_error(
                "default_journal",
                "全局模式直接上传 DOCX/Markdown 时必须选择默认子期刊。",
            )
        return cleaned_data


class ArticleImportConfirmForm(forms.Form):
    job_id = forms.IntegerField(widget=forms.HiddenInput())
    allow_suspicious_text = forms.BooleanField(
        required=False,
        label="按原文处理可疑文本",
        help_text="仅项目总负责人或超级管理员可用。",
    )
    override_reason = forms.CharField(
        required=False,
        min_length=8,
        max_length=500,
        label="强制处理理由",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("allow_suspicious_text") and not cleaned_data.get(
            "override_reason"
        ):
            self.add_error("override_reason", "强制处理时必须填写至少 8 个字符的理由。")
        return cleaned_data
