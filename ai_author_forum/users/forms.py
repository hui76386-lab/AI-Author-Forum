from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from ai_author_forum.journals.models import Journal, JournalEditorAssignment


class AccountCreateForm(forms.Form):
    username = forms.CharField(max_length=150, label="用户名")
    email = forms.EmailField(label="邮箱")
    display_name = forms.CharField(max_length=120, label="姓名")
    institution = forms.CharField(max_length=255, required=False, label="单位")
    job_title = forms.CharField(max_length=120, required=False, label="职务/职称")
    temporary_password = forms.CharField(
        label="临时密码",
        strip=False,
        widget=forms.PasswordInput,
    )
    is_super_admin_account = forms.BooleanField(
        required=False,
        label="超级管理员",
    )
    is_author_account = forms.BooleanField(
        required=False,
        label="作者",
        help_text="作者账号不获得 Wagtail 后台权限；还需为具体文章授予投稿关系。",
    )
    confirming_password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput,
        label="创建超级管理员时确认本人密码",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError("该邮箱已被使用。")
        return email

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        validate_password(password)
        return password


class JournalAssignmentForm(forms.Form):
    journal = forms.ModelChoiceField(
        queryset=Journal.objects.order_by("name", "pk"),
        label="子期刊",
    )
    role = forms.ChoiceField(
        choices=JournalEditorAssignment.Role.choices,
        label="角色",
    )
    responsibilities = forms.MultipleChoiceField(
        choices=JournalEditorAssignment.Responsibility.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="日常维护职责",
    )
    public_name = forms.CharField(max_length=255, label="公开姓名")
    public_affiliation = forms.CharField(
        max_length=500,
        required=False,
        label="公开单位",
    )
    public_role_label = forms.CharField(
        max_length=40,
        required=False,
        label="前台角色名称",
    )
    display_order = forms.IntegerField(min_value=0, initial=0, label="展示顺序")
    show_publicly = forms.BooleanField(
        required=False,
        initial=True,
        label="在编辑团队中公开",
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
        if role in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }:
            cleaned["responsibilities"] = list(
                JournalEditorAssignment.ALL_RESPONSIBILITIES
            )
        journal = cleaned.get("journal")
        if (
            journal
            and role
            in {
                JournalEditorAssignment.Role.CHIEF_EDITOR,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
            }
            and JournalEditorAssignment.objects.filter(
                journal=journal,
                role=role,
                is_active=True,
            ).exists()
        ):
            self.add_error("role", "该子期刊已有在任人员，请使用角色交接。")
        if role and not cleaned.get("public_role_label"):
            cleaned["public_role_label"] = (
                JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
            )
        return cleaned

    def as_service_payload(self):
        return {
            "journal": self.cleaned_data["journal"],
            "role": self.cleaned_data["role"],
            "responsibilities": self.cleaned_data["responsibilities"],
            "public_profile": {
                "public_name": self.cleaned_data["public_name"],
                "public_affiliation": self.cleaned_data["public_affiliation"],
                "public_role_label": self.cleaned_data["public_role_label"],
                "display_order": self.cleaned_data["display_order"],
                "show_publicly": self.cleaned_data["show_publicly"],
            },
        }


JournalAssignmentFormSet = forms.formset_factory(
    JournalAssignmentForm,
    extra=1,
    can_delete=True,
)


class AccountStatusForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea, label="原因")
    confirming_password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput,
        label="本人密码确认",
    )


class ResetPasswordForm(forms.Form):
    temporary_password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput,
        label="新临时密码",
    )
    confirming_password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput,
        label="重置超级管理员时确认本人密码",
    )

    def __init__(self, *args, user=None, requires_confirmation=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.requires_confirmation = requires_confirmation
        if not requires_confirmation:
            self.fields.pop("confirming_password")

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        validate_password(password, user=self.user)
        return password

    def clean_confirming_password(self):
        password = self.cleaned_data.get("confirming_password", "")
        if self.requires_confirmation and not password:
            raise ValidationError("高风险账号操作必须确认本人密码。")
        return password


class RequiredPasswordChangeForm(PasswordChangeForm):
    pass
