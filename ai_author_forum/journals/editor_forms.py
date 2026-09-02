import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from .models import Journal, JournalEditorAssignment, JournalStatus


class JournalProfileForm(forms.ModelForm):
    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if actor and actor.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists():
            self.fields["status"] = forms.ChoiceField(
                choices=JournalStatus.choices,
                label="前台发布状态",
                help_text="草稿不会进入前台；启用前请先完成主编辑和内容准备。",
            )
            self.fields["static_site_path"] = forms.CharField(
                max_length=255,
                required=False,
                label="静态主页输出路径",
                help_text="通常保持自动生成的 /journals/{slug}/index.html。",
            )
            self.initial["status"] = self.instance.status
            self.initial["static_site_path"] = self.instance.static_site_path

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


class JournalCreateForm(forms.ModelForm):
    """Small first-step form for creating a journal safely."""

    slug = forms.SlugField(
        max_length=255,
        validators=[
            RegexValidator(
                r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "Slug 只能使用小写字母、数字和连字符，例如 ai-research。",
            )
        ],
        help_text="用于前台地址：/journals/{slug}/，保存后不能直接修改。",
    )

    class Meta:
        model = Journal
        fields = ("name", "name_cn", "slug", "az_group", "sort_order")
        labels = {
            "name": "英文名称",
            "name_cn": "中文名称",
            "slug": "前台地址标识（slug）",
            "az_group": "A-Z 分组",
            "sort_order": "列表排序",
        }
        help_texts = {
            "name": "前台英文标题和后台识别名称。",
            "name_cn": "中文后台和中文前台显示名称，建议填写。",
            "az_group": "填写一个 A-Z 字母；非英文名称可填写 #。",
            "sort_order": "数值越小越靠前。",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["az_group"].widget.attrs.setdefault("maxlength", 1)

    def clean_az_group(self):
        value = self.cleaned_data["az_group"].strip().upper()
        if value and not re.fullmatch(r"[A-Z#]", value):
            raise ValidationError("A-Z 分组只能是 A-Z 或 #。")
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


class JournalEditorAccountCreateForm(forms.Form):
    role = forms.ChoiceField(
        choices=JournalEditorAssignment.Role.choices,
        initial=JournalEditorAssignment.Role.CHIEF_EDITOR,
        label="角色",
    )
    username = forms.CharField(
        max_length=150,
        validators=[UnicodeUsernameValidator()],
        label="用户名",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    temporary_password = forms.CharField(
        strip=False,
        label="初始密码",
        help_text="账号首次登录后必须修改密码。",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    email = forms.EmailField(label="邮箱")
    display_name = forms.CharField(max_length=120, label="姓名")
    institution = forms.CharField(max_length=255, required=False, label="单位")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if get_user_model().objects.filter(username=username).exists():
            raise ValidationError("该用户名已被使用。")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError("该邮箱已被使用。")
        return email

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        validate_password(password)
        return password


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
