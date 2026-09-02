from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_journals,
    is_super_admin,
)

from .issue_forms import (
    IssueArticleForm,
    PublicationIssueDraftForm,
    manageable_issue_journals,
)
from .issues import remove_issue_article, save_issue_article, save_issue_draft
from .models import (
    IssueArticle,
    Journal,
    JournalEditorAssignment,
    PublicationIssue,
    PublicationIssueScope,
)


def _visible_issues(user):
    queryset = PublicationIssue.objects.select_related("journal")
    if is_super_admin(user):
        return queryset
    journals = filter_accessible_journals(user, Journal.objects.all())
    return queryset.filter(
        scope=PublicationIssueScope.JOURNAL,
        journal__in=journals,
    )


def _can_manage_issue(user, issue):
    return is_super_admin(user) or bool(
        issue.journal_id
        and can_manage_journal(
            user,
            issue.journal,
            JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT,
        )
    )


def publication_issue_draft_admin(request, issue_id=None):
    if issue_id is None:
        issue = None
        if (
            not is_super_admin(request.user)
            and not manageable_issue_journals(request.user).exists()
        ):
            raise PermissionDenied
    else:
        issue = get_object_or_404(_visible_issues(request.user), pk=issue_id)
        if not _can_manage_issue(request.user, issue):
            raise PermissionDenied
    form = PublicationIssueDraftForm(
        request.POST or None,
        request.FILES or None,
        instance=issue,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            save_issue_draft(
                actor=request.user,
                issue=issue,
                values={name: form.cleaned_data[name] for name in form.fields},
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            form.add_error(None, exc)
        else:
            messages.success(request, "期次草稿已保存。")
            return redirect("journals_publication_issue_admin")
    return render(
        request,
        "wagtailadmin/journals/publication_issue_form.html",
        {
            "title": "编辑期次草稿" if issue else "新建期次草稿",
            "form": form,
            "issue": issue,
        },
    )


def issue_article_admin(request, assignment_id=None):
    visible_issues = _visible_issues(request.user)
    assignments = IssueArticle.objects.select_related(
        "issue", "issue__journal", "article"
    ).filter(issue__in=visible_issues)
    assignment = None
    if assignment_id is not None:
        assignment = get_object_or_404(assignments, pk=assignment_id)
        if not _can_manage_issue(request.user, assignment.issue):
            raise PermissionDenied
    if request.method == "POST" and request.POST.get("action") == "remove":
        target = get_object_or_404(assignments, pk=request.POST.get("assignment_id"))
        if not _can_manage_issue(request.user, target.issue):
            raise PermissionDenied
        try:
            remove_issue_article(actor=request.user, assignment=target)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "目录项已移除。")
        return redirect("journals_issue_article_admin")

    form = IssueArticleForm(
        request.POST or None,
        instance=assignment,
        user=request.user,
        initial={"issue": request.GET.get("issue")},
    )
    if request.method == "POST" and form.is_valid():
        try:
            save_issue_article(
                actor=request.user,
                assignment=assignment,
                values=form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            if isinstance(exc, PermissionDenied):
                raise
            form.add_error(None, exc)
        else:
            messages.success(request, "期次目录已保存。")
            return redirect("journals_issue_article_admin")
    return render(
        request,
        "wagtailadmin/journals/issue_article_admin.html",
        {
            "title": "期次文章目录",
            "assignments": assignments.order_by(
                "issue__publication_date", "issue_id", "sort_order", "pk"
            ),
            "form": form,
            "editing_assignment": assignment,
        },
    )
