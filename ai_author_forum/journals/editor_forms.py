from django import forms
from django.contrib.auth import get_user_model

from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from .models import Journal, JournalEditorAssignment


class JournalProfileForm(forms.ModelForm):
    class Meta:
        model = Journal
        fields = (
            "name",
            "name_cn",
            "az_group",
            "sort_order",
            "homepage_intro",
            "hero_kicker",
            "hero_primary_cta_text",
            "hero_primary_cta_url",
            "hero_image",
            "hero_image_alt",
            "cover_image",
            "metrics_image",
            "seo_title",
            "seo_description",
            "notes",
        )


class EditorialProfileForm(forms.Form):
    public_name = forms.CharField(max_length=255, label="前台姓名")
    public_affiliation = forms.CharField(
        max_length=500, required=False, label="前台单位"
    )
    public_role_label = forms.CharField(max_length=40, label="前台角色名称")
    display_order = forms.IntegerField(min_value=0, label="同组排序")
    show_publicly = forms.BooleanField(required=False, label="在前台展示")
    responsibilities = forms.MultipleChoiceField(
        required=False,
        choices=JournalEditorAssignment.Responsibility.choices,
        widget=forms.CheckboxSelectMultiple,
        label="日常职责",
    )

    def __init__(self, *args, assignment, can_manage, **kwargs):
        self.assignment = assignment
        self.can_manage = can_manage
        initial = {
            "public_name": assignment.public_name,
            "public_affiliation": assignment.public_affiliation,
            "public_role_label": assignment.public_role_label,
            "display_order": assignment.display_order,
            "show_publicly": assignment.show_publicly,
            "responsibilities": assignment.responsibilities,
        }
        initial.update(kwargs.pop("initial", {}))
        super().__init__(*args, initial=initial, **kwargs)
        if not can_manage:
            self.fields.pop("display_order")
            self.fields.pop("show_publicly")
            self.fields.pop("responsibilities")
        elif assignment.role != JournalEditorAssignment.Role.ASSOCIATE_EDITOR:
            self.fields.pop("responsibilities")

    def clean_public_role_label(self):
        value = self.cleaned_data["public_role_label"].strip()
        allowed = JournalEditorAssignment.ALLOWED_PUBLIC_ROLE_LABELS[
            self.assignment.role
        ]
        if value not in allowed:
            raise forms.ValidationError("前台角色名称与任命角色不匹配。")
        return value


class AppointEditorForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(), label="账号"
    )
    role = forms.ChoiceField(choices=JournalEditorAssignment.Role.choices, label="角色")
    responsibilities = forms.MultipleChoiceField(
        required=False,
        choices=JournalEditorAssignment.Responsibility.choices,
        widget=forms.CheckboxSelectMultiple,
        label="日常职责",
    )
    public_name = forms.CharField(max_length=255, required=False, label="前台姓名")
    public_affiliation = forms.CharField(
        max_length=500, required=False, label="前台单位"
    )
    public_role_label = forms.CharField(
        max_length=40, required=False, label="前台角色名称"
    )
    display_order = forms.IntegerField(min_value=0, initial=0, label="同组排序")
    show_publicly = forms.BooleanField(required=False, initial=True, label="在前台展示")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = (
            get_user_model()
            .objects.filter(is_active=True, account_status="active")
            .exclude(groups__name=SUPER_ADMIN_GROUP_NAME)
            .order_by("display_name", "username")
        )

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        responsibilities = cleaned.get("responsibilities") or []
        if (
            role == JournalEditorAssignment.Role.ASSOCIATE_EDITOR
            and not responsibilities
        ):
            self.add_error("responsibilities", "副编辑至少需要一项维护职责。")
        label = (cleaned.get("public_role_label") or "").strip()
        if (
            label
            and label
            not in JournalEditorAssignment.ALLOWED_PUBLIC_ROLE_LABELS.get(role, set())
        ):
            self.add_error("public_role_label", "前台角色名称与任命角色不匹配。")
        return cleaned


class ReplaceEditorForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(), label="接任账号"
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="交接原因")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = (
            get_user_model()
            .objects.filter(is_active=True, account_status="active")
            .exclude(groups__name=SUPER_ADMIN_GROUP_NAME)
            .order_by("display_name", "username")
        )


class EndAssignmentForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="结束原因")


class EditorialTeamSettingsForm(forms.Form):
    editorial_team_heading = forms.CharField(max_length=80, label="编辑团队标题")
    show_editorial_team_on_article_pages = forms.BooleanField(
        required=False, label="在文章页展示编辑团队"
    )
