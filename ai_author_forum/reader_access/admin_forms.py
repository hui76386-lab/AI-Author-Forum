from django import forms

from .models import ArticleInteractionPolicy, JournalInteractionPolicy


class JournalInteractionPolicyForm(forms.Form):
    default_comments_mode = forms.ChoiceField(
        label="默认评论模式", choices=JournalInteractionPolicy.CommentsMode.choices
    )
    default_pdf_download_enabled = forms.BooleanField(
        label="默认允许 PDF 下载", required=False
    )
    expected_version = forms.IntegerField(
        label="期望版本", min_value=0, widget=forms.HiddenInput
    )
    reason = forms.CharField(label="变更原因", required=False, max_length=500)


class ArticleInteractionPolicyForm(forms.Form):
    comments_policy = forms.ChoiceField(
        label="文章评论政策", choices=ArticleInteractionPolicy.CommentsPolicy.choices
    )
    pdf_download_policy = forms.ChoiceField(
        label="文章 PDF 政策",
        choices=ArticleInteractionPolicy.PdfDownloadPolicy.choices,
    )
    expected_version = forms.IntegerField(
        label="期望版本", min_value=0, widget=forms.HiddenInput
    )
    reason = forms.CharField(label="变更原因", required=False, max_length=500)


def require_reason(form):
    form.fields["reason"].required = True
    form.fields["reason"].help_text = (
        "超级管理员操作必须填写原因；所有变更会写入不可变审计日志。"
    )
