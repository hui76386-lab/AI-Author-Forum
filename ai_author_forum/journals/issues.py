from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

from .models import PublicationIssue, PublicationIssueStatus


def _require(actor, permission):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied
    if not actor.is_superuser and not actor.has_perm(permission):
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


@_audit_failures(AuditAction.PUBLISH)
@transaction.atomic
def publish_issue(issue: PublicationIssue, *, actor):
    _require(actor, "journals.publish_publication_issue")
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
    _require(actor, "journals.set_current_publication_issue")
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
    _require(actor, "journals.publish_publication_issue")
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
    _require(actor, "journals.rollback_publication_issue")
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
