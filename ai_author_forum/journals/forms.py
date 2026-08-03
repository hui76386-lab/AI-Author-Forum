from django import forms


class ImportPackageForm(forms.Form):
    package = forms.FileField(
        label="导入数据包",
        help_text="上传包含 journals/articles Excel 或 CSV 文件及可选 media/ 的 ZIP。系统会先执行逐行预览，不会在预览阶段写入业务数据。",
        widget=forms.FileInput(attrs={"accept": ".zip,application/zip"}),
    )
    csv_encoding = forms.ChoiceField(
        label="CSV 编码策略",
        choices=(("auto", "自动：UTF-8 / UTF-8-SIG"), ("gb18030", "显式使用 GB18030")),
        initial="auto",
        required=False,
        help_text="仅对 CSV 生效；Excel 文件不做编码选择。自动模式不会猜测或尝试 GBK。",
    )


class ConfirmImportForm(forms.Form):
    journal_job_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    article_job_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    csv_encoding = forms.CharField(required=False, widget=forms.HiddenInput())
    override_suspicious_text = forms.BooleanField(
        required=False,
        label="按原文导入可疑文本",
        help_text="仅超级管理员可用。系统不会自动转换或猜测恢复可疑文本。",
    )
    override_reason = forms.CharField(
        required=False,
        label="强制导入理由",
        min_length=8,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    publish_static_site = forms.BooleanField(
        required=False,
        initial=False,
        label="导入后生成现有已审核内容的静态站点",
        help_text="新导入文章仍为草稿；静态生成只包含已经审核、投放并满足生效条件的正式文章。",
    )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("journal_job_id") and not cleaned_data.get(
            "article_job_id"
        ):
            raise forms.ValidationError("必须先完成有效的导入预览。")
        if cleaned_data.get("override_suspicious_text") and not cleaned_data.get(
            "override_reason"
        ):
            self.add_error(
                "override_reason", "按原文导入时必须填写至少 8 个字符的理由。"
            )
        return cleaned_data
