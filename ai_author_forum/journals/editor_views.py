from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_journals,
    get_journal_editor_assignment,
    is_super_admin,
)

from .editor_forms import (
    AppointEditorForm,
    EditorialProfileForm,
    EditorialTeamSettingsForm,
    EndAssignmentForm,
    JournalEditorAccountCreateForm,
    JournalProfileForm,
    ReplaceEditorForm,
)
from .editor_services import (
    appoint_journal_editor,
    create_journal_editor_account,
    end_journal_editor_assignment,
    replace_chief_editor,
    replace_executive_editor,
    update_editor_assignment_profile,
    update_editorial_team_settings,
    update_journal_profile,
)
from .models import Journal, JournalEditorAssignment


def _error_text(exc):
    if hasattr(exc, "messages"):
        return "; ".join(exc.messages)
    return str(exc)


def _accessible_journals(user):
    return filter_accessible_journals(user, Journal.objects.all()).order_by(
        "sort_order", "name", "pk"
    )


def _can_manage_team(user, journal):
    if is_super_admin(user):
        return True
    assignment = get_journal_editor_assignment(user, journal)
    return bool(
        assignment
        and assignment.role
        in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
    )


def editorial_team_index(request, journal_id=None):
    journals = _accessible_journals(request.user)
    if journal_id is None:
        return render(
            request,
            "journals/admin/editorial_team_list.html",
            {"title": "编辑团队", "journals": journals},
        )

    journal = get_object_or_404(journals, pk=journal_id)
    can_manage = _can_manage_team(request.user, journal)
    can_assign = is_super_admin(request.user)
    assignments = list(
        JournalEditorAssignment.objects.filter(journal=journal, is_active=True)
        .select_related("user")
        .order_by("role", "display_order", "pk")
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create-account" and not can_assign:
            return HttpResponseForbidden("只有超级管理员可以创建期刊角色账号。")
        try:
            if action == "settings":
                form = EditorialTeamSettingsForm(request.POST, prefix="settings")
                if not form.is_valid():
                    raise ValidationError(form.errors.as_text())
                update_editorial_team_settings(
                    actor=request.user,
                    journal=journal,
                    values=form.cleaned_data,
                )
            elif action == "update":
                assignment = get_object_or_404(
                    JournalEditorAssignment,
                    pk=request.POST.get("assignment_id"),
                    journal=journal,
                    is_active=True,
                )
                row_can_manage = can_manage
                if not row_can_manage and assignment.user_id != request.user.pk:
                    raise PermissionDenied
                form = EditorialProfileForm(
                    request.POST,
                    assignment=assignment,
                    can_manage=row_can_manage,
                    prefix=f"profile-{assignment.pk}",
                )
                if not form.is_valid():
                    raise ValidationError(form.errors.as_text())
                profile = {
                    key: value
                    for key, value in form.cleaned_data.items()
                    if key != "responsibilities"
                }
                update_editor_assignment_profile(
                    actor=request.user,
                    assignment=assignment,
                    responsibilities=form.cleaned_data.get("responsibilities"),
                    public_profile=profile,
                )
            elif action == "appoint":
                if not can_assign:
                    raise PermissionDenied
                form = AppointEditorForm(request.POST, prefix="appoint")
                if not form.is_valid():
                    raise ValidationError(form.errors.as_text())
                data = form.cleaned_data
                appoint_journal_editor(
                    actor=request.user,
                    user=data["user"],
                    journal=journal,
                    role=data["role"],
                    responsibilities=data["responsibilities"],
                    public_profile={
                        "public_name": data["public_name"],
                        "public_affiliation": data["public_affiliation"],
                        "public_role_label": data["public_role_label"],
                        "display_order": data["display_order"],
                        "show_publicly": data["show_publicly"],
                    },
                )
            elif action == "create-account":
                form = JournalEditorAccountCreateForm(
                    request.POST, prefix="create-account"
                )
                if not form.is_valid():
                    raise ValidationError(form.errors.as_text())
                create_journal_editor_account(
                    actor=request.user,
                    journal=journal,
                    **form.cleaned_data,
                )
            elif action == "replace":
                if not can_assign:
                    raise PermissionDenied
                assignment = get_object_or_404(
                    JournalEditorAssignment,
                    pk=request.POST.get("assignment_id"),
                    journal=journal,
                    is_active=True,
                    role__in=(
                        JournalEditorAssignment.Role.CHIEF_EDITOR,
                        JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
                    ),
                )
                form = ReplaceEditorForm(
                    request.POST, prefix=f"replace-{assignment.pk}"
                )
                if not form.is_valid():
                    raise ValidationError(form.errors.as_text())
                service = (
                    replace_chief_editor
                    if assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR
                    else replace_executive_editor
                )
                service(
                    actor=request.user,
                    journal=journal,
                    new_user=form.cleaned_data["user"],
                    reason=form.cleaned_data["reason"],
                )
            elif action == "end":
                if not can_assign:
                    raise PermissionDenied
                assignment = get_object_or_404(
                    JournalEditorAssignment,
                    pk=request.POST.get("assignment_id"),
                    journal=journal,
                    is_active=True,
                )
                form = EndAssignmentForm(request.POST, prefix=f"end-{assignment.pk}")
                if not form.is_valid():
                    raise ValidationError(form.errors.as_text())
                end_journal_editor_assignment(
                    actor=request.user,
                    assignment=assignment,
                    reason=form.cleaned_data["reason"],
                )
            else:
                raise ValidationError("不支持的编辑团队操作。")
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            messages.error(request, _error_text(exc))
        else:
            if action == "create-account":
                messages.success(request, "角色账号已创建并加入当前期刊。")
            else:
                messages.success(request, "编辑团队已更新。")
        return redirect("journals_editorial_team", journal_id=journal.pk)

    rows = []
    for assignment in assignments:
        can_edit_row = can_manage or assignment.user_id == request.user.pk
        rows.append(
            {
                "assignment": assignment,
                "can_edit": can_edit_row,
                "profile_form": (
                    EditorialProfileForm(
                        assignment=assignment,
                        can_manage=can_manage,
                        prefix=f"profile-{assignment.pk}",
                    )
                    if can_edit_row
                    else None
                ),
                "replace_form": (
                    ReplaceEditorForm(prefix=f"replace-{assignment.pk}")
                    if can_assign
                    and assignment.role
                    in {
                        JournalEditorAssignment.Role.CHIEF_EDITOR,
                        JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
                    }
                    else None
                ),
                "end_form": (
                    EndAssignmentForm(prefix=f"end-{assignment.pk}")
                    if can_assign
                    else None
                ),
            }
        )
    return render(
        request,
        "journals/admin/editorial_team.html",
        {
            "title": f"编辑团队 / {journal}",
            "journal": journal,
            "rows": rows,
            "can_manage": can_manage,
            "can_assign": can_assign,
            "settings_form": (
                EditorialTeamSettingsForm(
                    prefix="settings",
                    initial={
                        "editorial_team_heading": journal.editorial_team_heading,
                        "show_editorial_team_on_article_pages": (
                            journal.show_editorial_team_on_article_pages
                        ),
                    },
                )
                if can_manage
                else None
            ),
            "appoint_form": AppointEditorForm(prefix="appoint") if can_assign else None,
            "create_account_form": (
                JournalEditorAccountCreateForm(prefix="create-account")
                if can_assign
                else None
            ),
            "locked_responsibilities": (
                JournalEditorAssignment.Responsibility.choices if can_assign else ()
            ),
        },
    )


def journal_profile(request, journal_id):
    journal = get_object_or_404(_accessible_journals(request.user), pk=journal_id)
    if not can_manage_journal(
        request.user,
        journal,
        JournalEditorAssignment.Responsibility.JOURNAL_PROFILE,
    ):
        raise PermissionDenied
    form = JournalProfileForm(
        request.POST or None,
        instance=journal,
        actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_journal_profile(
                actor=request.user,
                journal=journal,
                values={name: form.cleaned_data[name] for name in form.fields},
            )
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        else:
            messages.success(request, "子期刊资料已更新。")
            return redirect("journals:workspace", journal_id=journal.pk)
    return render(
        request,
        "journals/admin/journal_profile.html",
        {"title": f"子期刊资料 / {journal}", "journal": journal, "form": form},
    )
