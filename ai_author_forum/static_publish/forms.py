from django import forms

from ai_author_forum.journals.models import Journal

from .models import StaticManifest, StaticPublishJob, StaticPublishTarget


class PublishForm(forms.Form):
    scope = forms.ChoiceField(
        label="发布范围",
        choices=(
            ("", "请选择发布范围"),
            (
                StaticPublishJob.Scope.FULL,
                "全站发布：重建整个站点（首次发布或全局改版）",
            ),
            (
                StaticPublishJob.Scope.JOURNAL,
                "本刊发布：更新一个子期刊及其前台目录入口（推荐）",
            ),
            (
                StaticPublishJob.Scope.SELECTIVE,
                "指定路径：仅用于已知输出路径的故障修复",
            ),
        ),
    )
    paths = forms.CharField(
        label="指定路径",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "/articles/example/"}),
        help_text="每行一个公开 URL 或输出路径；选择“指定路径”时必填。",
    )
    journal = forms.ModelChoiceField(
        label="目标子期刊",
        queryset=Journal.objects.order_by("name", "pk"),
        required=False,
        empty_label="请选择子期刊",
        help_text="“本刊发布”会自动计算该刊的主页、栏目、期次和关联文章目标。",
    )

    def clean(self):
        cleaned = super().clean()
        paths = [
            line.strip()
            for line in cleaned.get("paths", "").splitlines()
            if line.strip()
        ]
        if cleaned.get("scope") == StaticPublishJob.Scope.JOURNAL and not cleaned.get(
            "journal"
        ):
            self.add_error("journal", "选择“本刊发布”时必须选择一个子期刊。")
        if cleaned.get("scope") == StaticPublishJob.Scope.SELECTIVE and not paths:
            self.add_error("paths", "指定路径发布至少需要填写一个路径。")
        cleaned["paths"] = paths
        return cleaned

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(("scope", "journal", "paths"))


class PublishJobFilterForm(forms.Form):
    status = forms.ChoiceField(
        label="状态",
        required=False,
        choices=(("", "全部状态"), *StaticPublishJob.Status.choices),
    )
    scope = forms.ChoiceField(
        label="范围",
        required=False,
        choices=(("", "全部范围"), *StaticPublishJob.Scope.choices),
    )
    target_status = forms.ChoiceField(
        label="目标状态",
        required=False,
        choices=(("", "全部目标状态"), *StaticPublishTarget.Status.choices),
    )
    manifest_status = forms.ChoiceField(
        label="版本状态",
        required=False,
        choices=(
            ("", "全部版本"),
            ("active", "当前活动版本"),
            ("rollback", "可回滚版本"),
        ),
    )
    triggered_by = forms.CharField(label="发起人", required=False)
    created_from = forms.DateField(
        label="开始日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    created_to = forms.DateField(
        label="结束日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )


class TargetFilterForm(forms.Form):
    status = forms.ChoiceField(
        label="状态",
        required=False,
        choices=(("", "全部状态"), *StaticPublishTarget.Status.choices),
    )
    path = forms.CharField(label="路径关键词", required=False)
    target_type = forms.CharField(label="来源类型", required=False)
    error_category = forms.ChoiceField(
        label="错误分类",
        required=False,
        choices=(("", "全部错误"), *StaticPublishTarget.ErrorCategory.choices),
    )


class RollbackSelectForm(forms.Form):
    version = forms.ModelChoiceField(
        label="目标版本",
        queryset=StaticManifest.objects.none(),
        empty_label="请选择历史版本",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].queryset = StaticManifest.objects.order_by("-created_at")


class RollbackForm(forms.Form):
    version = forms.ModelChoiceField(
        label="目标版本",
        queryset=StaticManifest.objects.none(),
        empty_label="请选择历史版本",
    )
    reason = forms.CharField(
        label="回滚原因",
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        min_length=5,
        help_text="必填，将写入任务记录和审计日志。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].queryset = StaticManifest.objects.order_by("-created_at")

    def clean_version(self):
        return self.cleaned_data["version"].version
