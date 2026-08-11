from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from ai_author_forum.site_settings.access_control import (
    can_publish_issue,
    get_journal_editor_assignment,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

from .models import (
    IssueArticle,
    JournalEditorAssignment,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)


def _require(actor, issue, action):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied
    if is_super_admin(actor):
        return
    if action in {"publish", "set_current"} and can_publish_issue(actor, issue):
        return
    assignment = get_journal_editor_assignment(actor, issue.journal)
    if (
        action in {"archive", "rollback"}
        and assignment
        and (assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR)
    ):
        return
    if action not in {"publish", "set_current", "archive", "rollback"}:
        raise PermissionDenied
    raise PermissionDenied


def _audit(*, actor, action, issue, message, metadata=None, status=AuditStatus.SUCCESS):
    return record_audit_event(
        actor=actor,
        action=action,
        status=status,
        target=issue,
        message=message,
        metadata=metadata or {},
    )


def _audit_failures(action):
    def decorator(operation):
        @wraps(operation)
        def wrapped(issue: PublicationIssue, *, actor):
            try:
                return operation(issue, actor=actor)
            except Exception as exc:
                _audit(
                    actor=actor if getattr(actor, "is_authenticated", False) else None,
                    action=action,
                    issue=issue,
                    status=AuditStatus.FAILURE,
                    message=f"Publication issue action failed: {exc}",
                    metadata={
                        "exception": type(exc).__name__,
                        "status": getattr(issue, "status", ""),
                        "is_current": bool(getattr(issue, "is_current", False)),
                    },
                )
                raise

        return wrapped

    return decorator


def _can_manage_issue_draft(actor, issue):
    from ai_author_forum.site_settings.access_control import can_manage_journal

    if is_super_admin(actor):
        return True
    return bool(
        issue.scope == PublicationIssueScope.JOURNAL
        and issue.journal_id
        and can_manage_journal(
            actor,
            issue.journal,
            JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT,
        )
    )


@transaction.atomic
def save_issue_draft(*, actor, values, issue=None):
    if issue is None:
        issue = PublicationIssue()
        created = True
    else:
        issue = (
            PublicationIssue.objects.select_for_update()
            .select_related("journal")
            .get(pk=issue.pk)
        )
        created = False
        if not _can_manage_issue_draft(actor, issue):
            raise PermissionDenied("无权维护该期次草稿。")
        if issue.status != PublicationIssueStatus.DRAFT:
            raise ValidationError("已发布或归档期次必须先回滚为草稿后才能修改。")
    for field, value in values.items():
        setattr(issue, field, value)
    if not _can_manage_issue_draft(actor, issue):
        raise PermissionDenied("无权维护该期次草稿。")
    issue.status = PublicationIssueStatus.DRAFT
    issue.is_current = False
    issue.full_clean()
    issue.save()
    _audit(
        actor=actor,
        action=AuditAction.CONFIGURE,
        issue=issue,
        message=(
            "Publication issue draft created."
            if created
            else "Publication issue draft updated."
        ),
        metadata={
            "operation": "create_issue_draft" if created else "update_issue_draft"
        },
    )
    return issue


@transaction.atomic
def save_issue_article(*, actor, values, assignment=None):
    if assignment is None:
        assignment = IssueArticle()
        created = True
    else:
        assignment = (
            IssueArticle.objects.select_for_update(of=("self",))
            .select_related("issue", "issue__journal")
            .get(pk=assignment.pk)
        )
        created = False
        if not _can_manage_issue_draft(actor, assignment.issue):
            raise PermissionDenied("无权维护该期次目录。")
    issue = (
        PublicationIssue.objects.select_for_update(of=("self",))
        .select_related("journal")
        .get(pk=values["issue"].pk)
    )
    if issue.status != PublicationIssueStatus.DRAFT:
        raise ValidationError("只有期次草稿的文章目录可以修改。")
    if not _can_manage_issue_draft(actor, issue):
        raise PermissionDenied("无权维护该期次目录。")
    assignment.issue = issue
    assignment.article = values["article"]
    assignment.section_label = values.get("section_label", "")
    assignment.sort_order = values.get("sort_order", 0)
    assignment.full_clean()
    assignment.save()
    _audit(
        actor=actor,
        action=AuditAction.CONFIGURE,
        issue=issue,
        message="Issue article created." if created else "Issue article updated.",
        metadata={
            "operation": "create_issue_article" if created else "update_issue_article",
            "issue_article_id": assignment.pk,
            "article_id": assignment.article_id,
        },
    )
    return assignment


@transaction.atomic
def remove_issue_article(*, actor, assignment):
    assignment = (
        IssueArticle.objects.select_for_update()
        .select_related("issue", "issue__journal")
        .get(pk=assignment.pk)
    )
    issue = PublicationIssue.objects.select_for_update().get(pk=assignment.issue_id)
    if issue.status != PublicationIssueStatus.DRAFT:
        raise ValidationError("已发布或归档期次的目录项不得硬删除。")
    if not _can_manage_issue_draft(actor, issue):
        raise PermissionDenied("无权维护该期次目录。")
    assignment_id = assignment.pk
    article_id = assignment.article_id
    _audit(
        actor=actor,
        action=AuditAction.CONFIGURE,
        issue=issue,
        message="Issue article removed from draft.",
        metadata={
            "operation": "remove_issue_article",
            "issue_article_id": assignment_id,
            "article_id": article_id,
        },
    )
    assignment.delete()


@_audit_failures(AuditAction.PUBLISH)
@transaction.atomic
def publish_issue(issue: PublicationIssue, *, actor):
    _require(actor, issue, "publish")
    issue = PublicationIssue.objects.select_for_update().get(pk=issue.pk)
    assignments = list(
        issue.issue_articles.select_related("article", "issue", "issue__journal")
    )
    if not assignments:
        raise ValidationError(
            "A publication issue must contain at least one approved article before publication."
        )
    issue.status = PublicationIssueStatus.PUBLISHED
    issue.full_clean()
    for assignment in assignments:
        assignment.full_clean()
    issue.save(update_fields=("status", "updated_at"))
    _audit(
        actor=actor,
        action=AuditAction.PUBLISH,
        issue=issue,
        message="Publication issue published.",
        metadata={"status": issue.status, "is_current": issue.is_current},
    )
    return issue


@_audit_failures(AuditAction.CONFIGURE)
@transaction.atomic
def set_current_issue(issue: PublicationIssue, *, actor):
    _require(actor, issue, "set_current")
    issue = PublicationIssue.objects.select_for_update().get(pk=issue.pk)
    if issue.status != PublicationIssueStatus.PUBLISHED:
        raise ValidationError("Only a published issue can be current.")
    scope = PublicationIssue.objects.select_for_update().filter(scope=issue.scope)
    if issue.journal_id:
        scope = scope.filter(journal_id=issue.journal_id)
    else:
        scope = scope.filter(journal__isnull=True)
    previous_ids = list(scope.filter(is_current=True).values_list("pk", flat=True))
    scope.exclude(pk=issue.pk).filter(is_current=True).update(is_current=False)
    issue.is_current = True
    issue.full_clean()
    issue.save(update_fields=("is_current", "updated_at"))
    _audit(
        actor=actor,
        action=AuditAction.CONFIGURE,
        issue=issue,
        message="Publication issue set as current.",
        metadata={"previous_current_issue_ids": previous_ids},
    )
    return issue


@_audit_failures(AuditAction.CONFIGURE)
@transaction.atomic
def archive_issue(issue: PublicationIssue, *, actor):
    _require(actor, issue, "archive")
    issue = PublicationIssue.objects.select_for_update().get(pk=issue.pk)
    previous = {"status": issue.status, "is_current": issue.is_current}
    issue.status = PublicationIssueStatus.ARCHIVED
    issue.is_current = False
    issue.full_clean()
    issue.save(update_fields=("status", "is_current", "updated_at"))
    _audit(
        actor=actor,
        action=AuditAction.CONFIGURE,
        issue=issue,
        message="Publication issue archived.",
        metadata={"previous": previous},
    )
    return issue


@_audit_failures(AuditAction.ROLLBACK)
@transaction.atomic
def rollback_issue(issue: PublicationIssue, *, actor):
    _require(actor, issue, "rollback")
    issue = PublicationIssue.objects.select_for_update().get(pk=issue.pk)
    previous = {"status": issue.status, "is_current": issue.is_current}
    issue.status = PublicationIssueStatus.DRAFT
    issue.is_current = False
    issue.full_clean()
    issue.save(update_fields=("status", "is_current", "updated_at"))
    _audit(
        actor=actor,
        action=AuditAction.ROLLBACK,
        issue=issue,
        message="Publication issue rolled back to draft.",
        metadata={"previous": previous},
    )
    return issue
