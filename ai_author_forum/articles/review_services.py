from __future__ import annotations

from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ai_author_forum.journals.models import JournalEditorAssignment
from ai_author_forum.site_settings.access_control import (
    can_final_review,
    can_initial_review,
    can_manage_article,
    can_submit_submission,
    get_journal_editor_assignment,
    is_super_admin,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

from .models import ArticlePage, ArticleReviewRecord, ArticleRevisionConflict


class ArticleStateConflict(ValidationError):
    pass


def _request_uuid(request_id) -> UUID:
    if not request_id:
        raise ValidationError({"request_id": "审核动作必须提供 request id。"})
    try:
        return request_id if isinstance(request_id, UUID) else UUID(str(request_id))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"request_id": "request id 必须是有效 UUID。"}) from exc


def _latest_revision(article, actor):
    revision = article.get_latest_revision()
    if revision is None:
        revision = article.save_revision(
            user=actor,
            changed=False,
            bypass_article_permission_check=True,
        )
    return revision


def _validate_expected(*, article, expected_state, expected_revision_id, actor):
    if expected_state and article.review_status != expected_state:
        raise ArticleStateConflict(
            f"文章状态已从 {expected_state} 变为 {article.review_status}，请刷新后重试。"
        )
    revision = _latest_revision(article, actor)
    if str(revision.pk) != str(expected_revision_id or ""):
        raise ArticleRevisionConflict(
            "文章在页面打开后已产生新 revision，请刷新后重新操作。"
        )
    return revision


def _role_snapshot(actor, assignment):
    if assignment is not None:
        return assignment.role
    if is_super_admin(actor):
        return "super_admin"
    return ""


def _write_review_projection(article, **values):
    """Persist only service-owned review fields on the locked canonical row."""
    ArticlePage.objects.filter(pk=article.pk).update(**values)
    for field_name, value in values.items():
        setattr(article, field_name, value)


def _existing_review_result(*, request_id, article, expected_action=None):
    record = ArticleReviewRecord.objects.filter(request_id=request_id).first()
    if record is None:
        return None
    if record.article_id != article.pk:
        raise ArticleStateConflict("该 request id 已用于其他文章。")
    if expected_action and record.action != expected_action:
        raise ArticleStateConflict("该 request id 已用于其他审核动作。")
    return record


def _record_audit(
    *, actor, article, record, message, comment="", old_state=None, new_state=None
):
    return AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=article,
        message=message,
        request_id=str(record.request_id),
        metadata={
            "review_record_id": record.pk,
            "review_revision_id": record.revision_id,
            "stage": record.stage,
            "action": record.action,
            "reviewer_role": record.reviewer_role,
            "comment_present": bool(comment),
            "author_visible_comment_present": bool(record.author_visible_comment),
            "submission_owner_id": record.submission_owner_id,
            "submission_journal_id": record.submission_journal_id,
            "content_sha256": record.content_sha256,
            "old_state": old_state,
            "new_state": new_state,
        },
    )


def _existing_assignment_result(*, request_id, article, editor_id):
    audit = AuditLog.objects.filter(
        request_id=str(request_id),
        metadata__operation__in=(
            "claim_initial_review",
            "reassign_initial_review",
        ),
    ).first()
    if audit is None:
        return None
    if audit.target_id != str(article.pk) or str(
        audit.metadata.get("assigned_initial_editor_id")
    ) != str(editor_id):
        raise ArticleStateConflict("该 request id 已用于其他初审分配。")
    return ArticlePage.objects.get(pk=article.pk)


def _clear_invalid_initial_assignment(article):
    if article.assigned_initial_editor_id is None:
        return False
    remains_effective = (
        JournalEditorAssignment.objects.effective()
        .filter(
            user_id=article.assigned_initial_editor_id,
            journal_id=article.primary_journal_id,
        )
        .exists()
    )
    if remains_effective:
        return False
    article.assigned_initial_editor = None
    article.assigned_by = None
    article.assigned_at = None
    article.assignment_request_id = None
    article.save(bypass_article_permission_check=True)
    return True


@transaction.atomic
def submit_article_for_initial_review(
    *,
    actor,
    article,
    expected_state,
    expected_revision_id,
    request_id,
    comment="",
    source="editor",
):
    request_uuid = _request_uuid(request_id)
    existing = _existing_review_result(
        request_id=request_uuid,
        article=article,
        expected_action=ArticleReviewRecord.Action.SUBMIT,
    )
    if existing:
        return existing
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    existing = _existing_review_result(
        request_id=request_uuid,
        article=locked,
        expected_action=ArticleReviewRecord.Action.SUBMIT,
    )
    if existing:
        return existing
    author_source = source == "author"
    if author_source and not can_submit_submission(actor, locked):
        raise PermissionDenied("无权以作者身份提交该文章初审。")
    if not author_source and not can_manage_article(actor, locked):
        raise PermissionDenied("无权提交该文章初审。")
    if locked.review_status != ArticlePage.ReviewStatus.DRAFT:
        raise ArticleStateConflict("只有草稿可以提交初审；拒绝状态必须先重新开启。")
    revision = _validate_expected(
        article=locked,
        expected_state=expected_state,
        expected_revision_id=expected_revision_id,
        actor=actor,
    )
    from .category_services import validate_article_category_revision

    validate_article_category_revision(
        article=locked,
        revision_content=revision.content,
        action="submit",
    )
    assignment = (
        None
        if author_source
        else get_journal_editor_assignment(actor, locked.primary_journal)
    )
    from .author_services import revision_sha256
    from .models import ArticleAuthorship

    submission_owner = (
        ArticleAuthorship.objects.effective()
        .filter(article=locked, role=ArticleAuthorship.Role.OWNER)
        .first()
    )
    if author_source and submission_owner is None:
        raise ValidationError("作者投稿缺少有效投稿负责人。")
    submitted_at = timezone.now()
    locked.review_status = ArticlePage.ReviewStatus.SUBMITTED
    locked.rejected_version = None
    locked.has_ever_been_submitted = True
    locked.first_submitted_at = locked.first_submitted_at or submitted_at
    locked.last_submitted_at = submitted_at
    locked.save(bypass_article_permission_check=True)
    record = ArticleReviewRecord.objects.create(
        article=locked,
        stage=ArticleReviewRecord.Stage.INITIAL,
        action=ArticleReviewRecord.Action.SUBMIT,
        revision=revision,
        reviewer=actor,
        journal_editor_assignment=assignment,
        reviewer_role="author" if author_source else _role_snapshot(actor, assignment),
        request_id=request_uuid,
        comment=comment.strip(),
        content_sha256=revision_sha256(revision),
        submission_owner=submission_owner,
        submission_journal=locked.primary_journal,
        authorship_updated_at=getattr(submission_owner, "updated_at", None),
    )
    _record_audit(
        actor=actor,
        article=locked,
        record=record,
        message="文章已提交初审。",
        comment=comment.strip(),
        old_state=ArticlePage.ReviewStatus.DRAFT,
        new_state=locked.review_status,
    )
    from wagtail.models import WorkflowPage

    from .wagtail_hooks import _get_or_create_article_workflow

    workflow = _get_or_create_article_workflow()
    WorkflowPage.objects.update_or_create(
        page=locked,
        defaults={"workflow": workflow},
    )
    if workflow is not None and not locked.workflow_in_progress:
        workflow.start(locked, actor)
    return record


@transaction.atomic
def claim_initial_review(
    *,
    actor,
    article,
    expected_state,
    expected_revision_id,
    request_id,
):
    request_uuid = _request_uuid(request_id)
    historical = _existing_assignment_result(
        request_id=request_uuid,
        article=article,
        editor_id=actor.pk,
    )
    if historical:
        return historical
    prior = ArticlePage.objects.filter(assignment_request_id=request_uuid).first()
    if prior:
        if prior.pk != article.pk or prior.assigned_initial_editor_id != actor.pk:
            raise ArticleStateConflict("该 request id 已用于其他初审分配。")
        return prior
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    historical = _existing_assignment_result(
        request_id=request_uuid,
        article=locked,
        editor_id=actor.pk,
    )
    if historical:
        return historical
    assignment = get_journal_editor_assignment(actor, locked.primary_journal)
    if assignment is None:
        raise PermissionDenied("只有本刊有效编辑可以认领初审。")
    _validate_expected(
        article=locked,
        expected_state=expected_state,
        expected_revision_id=expected_revision_id,
        actor=actor,
    )
    if locked.review_status != ArticlePage.ReviewStatus.SUBMITTED:
        raise ArticleStateConflict("文章已不在待初审状态。")
    _clear_invalid_initial_assignment(locked)
    if locked.assigned_initial_editor_id is not None:
        raise ArticleStateConflict("文章已被其他编辑认领。")
    locked.assigned_initial_editor = actor
    locked.assigned_by = actor
    locked.assigned_at = timezone.now()
    locked.assignment_request_id = request_uuid
    locked.save(bypass_article_permission_check=True)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="认领文章初审。",
        request_id=str(request_uuid),
        metadata={
            "operation": "claim_initial_review",
            "assigned_initial_editor_id": actor.pk,
            "assignment_id": assignment.pk,
        },
    )
    return locked


@transaction.atomic
def reassign_initial_review(
    *,
    actor,
    article,
    new_editor,
    reason,
    expected_state,
    expected_revision_id,
    request_id,
):
    request_uuid = _request_uuid(request_id)
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "改派初审必须填写原因。"})
    historical = _existing_assignment_result(
        request_id=request_uuid,
        article=article,
        editor_id=new_editor.pk,
    )
    if historical:
        return historical
    prior = ArticlePage.objects.filter(assignment_request_id=request_uuid).first()
    if prior:
        if prior.pk != article.pk or prior.assigned_initial_editor_id != new_editor.pk:
            raise ArticleStateConflict("该 request id 已用于其他初审分配。")
        return prior
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    historical = _existing_assignment_result(
        request_id=request_uuid,
        article=locked,
        editor_id=new_editor.pk,
    )
    if historical:
        return historical
    actor_assignment = get_journal_editor_assignment(actor, locked.primary_journal)
    if not is_super_admin(actor) and (
        actor_assignment is None
        or actor_assignment.role
        not in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
    ):
        raise PermissionDenied("只有本刊主编辑或常务副编辑可以改派初审。")
    new_assignment = get_journal_editor_assignment(new_editor, locked.primary_journal)
    if new_assignment is None:
        raise ValidationError("被分配人不是本刊有效编辑。")
    _validate_expected(
        article=locked,
        expected_state=expected_state,
        expected_revision_id=expected_revision_id,
        actor=actor,
    )
    if locked.review_status != ArticlePage.ReviewStatus.SUBMITTED:
        raise ArticleStateConflict("文章已不在待初审状态。")
    previous_editor_id = locked.assigned_initial_editor_id
    locked.assigned_initial_editor = new_editor
    locked.assigned_by = actor
    locked.assigned_at = timezone.now()
    locked.assignment_request_id = request_uuid
    locked.save(bypass_article_permission_check=True)
    AuditLog.record(
        action=AuditAction.PERMISSION,
        status=AuditStatus.SUCCESS,
        actor=actor,
        target=locked,
        message="改派文章初审。",
        request_id=str(request_uuid),
        metadata={
            "operation": "reassign_initial_review",
            "reason": reason,
            "previous_editor_id": previous_editor_id,
            "assigned_initial_editor_id": new_editor.pk,
            "assignment_id": new_assignment.pk,
        },
    )
    return locked


@transaction.atomic
def initial_review_article(
    *,
    actor,
    article,
    action,
    comment,
    expected_state,
    expected_revision_id,
    request_id,
    author_visible_comment=None,
):
    request_uuid = _request_uuid(request_id)
    action_map = {
        "approve": ArticleReviewRecord.Action.INITIAL_APPROVE,
        "return": ArticleReviewRecord.Action.INITIAL_RETURN,
        "reject": ArticleReviewRecord.Action.INITIAL_REJECT,
    }
    record_action = action_map.get(action, action)
    if record_action not in action_map.values():
        raise ValidationError({"action": "不支持的初审动作。"})
    existing = _existing_review_result(
        request_id=request_uuid,
        article=article,
        expected_action=record_action,
    )
    if existing:
        return existing
    comment = comment.strip()
    if author_visible_comment is None:
        author_visible_comment = (
            comment
            if record_action
            in {
                ArticleReviewRecord.Action.INITIAL_RETURN,
                ArticleReviewRecord.Action.INITIAL_REJECT,
            }
            else ""
        )
    author_visible_comment = str(author_visible_comment or "").strip()
    if (
        record_action
        in {
            ArticleReviewRecord.Action.INITIAL_RETURN,
            ArticleReviewRecord.Action.INITIAL_REJECT,
        }
        and not comment
    ):
        raise ValidationError({"comment": "初审退回或拒绝必须填写意见。"})
    if (
        record_action
        in {
            ArticleReviewRecord.Action.INITIAL_RETURN,
            ArticleReviewRecord.Action.INITIAL_REJECT,
        }
        and not author_visible_comment
    ):
        raise ValidationError(
            {"author_visible_comment": "初审退回或拒绝必须填写作者可见原因。"}
        )
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    existing = _existing_review_result(
        request_id=request_uuid,
        article=locked,
        expected_action=record_action,
    )
    if existing:
        return existing
    revision = _validate_expected(
        article=locked,
        expected_state=expected_state,
        expected_revision_id=expected_revision_id,
        actor=actor,
    )
    if not can_initial_review(actor, locked):
        raise PermissionDenied("无权执行该文章初审。")
    if is_super_admin(actor) and not comment:
        raise ValidationError({"comment": "超级管理员应急初审必须填写原因。"})
    assignment = get_journal_editor_assignment(actor, locked.primary_journal)
    if record_action == ArticleReviewRecord.Action.INITIAL_APPROVE:
        locked.review_status = ArticlePage.ReviewStatus.PENDING_FINAL
    elif record_action == ArticleReviewRecord.Action.INITIAL_RETURN:
        locked.review_status = ArticlePage.ReviewStatus.DRAFT
    else:
        locked.review_status = ArticlePage.ReviewStatus.REJECTED
        locked.rejected_version = revision
    if record_action != ArticleReviewRecord.Action.INITIAL_APPROVE:
        locked.assigned_initial_editor = None
        locked.assigned_by = None
        locked.assigned_at = None
        locked.assignment_request_id = None
    locked.save(bypass_article_permission_check=True)
    record = ArticleReviewRecord.objects.create(
        article=locked,
        stage=ArticleReviewRecord.Stage.INITIAL,
        action=record_action,
        revision=revision,
        reviewer=actor,
        journal_editor_assignment=assignment,
        reviewer_role=_role_snapshot(actor, assignment),
        request_id=request_uuid,
        comment=comment,
        author_visible_comment=author_visible_comment,
    )
    _record_audit(
        actor=actor,
        article=locked,
        record=record,
        message="完成文章初审动作。",
        comment=comment,
        old_state=ArticlePage.ReviewStatus.SUBMITTED,
        new_state=locked.review_status,
    )
    return record


@transaction.atomic
def final_review_article(
    *,
    actor,
    article,
    action,
    comment,
    expected_state,
    expected_revision_id,
    request_id,
    author_visible_comment=None,
):
    request_uuid = _request_uuid(request_id)
    action_map = {
        "approve": ArticleReviewRecord.Action.FINAL_APPROVE,
        "return": ArticleReviewRecord.Action.FINAL_RETURN,
        "reject": ArticleReviewRecord.Action.FINAL_REJECT,
    }
    record_action = action_map.get(action, action)
    if record_action not in action_map.values():
        raise ValidationError({"action": "不支持的终审动作。"})
    existing = _existing_review_result(
        request_id=request_uuid,
        article=article,
        expected_action=record_action,
    )
    if existing:
        return existing
    comment = comment.strip()
    if author_visible_comment is None:
        author_visible_comment = (
            comment
            if record_action
            in {
                ArticleReviewRecord.Action.FINAL_RETURN,
                ArticleReviewRecord.Action.FINAL_REJECT,
            }
            else ""
        )
    author_visible_comment = str(author_visible_comment or "").strip()
    if (
        record_action
        in {
            ArticleReviewRecord.Action.FINAL_RETURN,
            ArticleReviewRecord.Action.FINAL_REJECT,
        }
        and not comment
    ):
        raise ValidationError({"comment": "终审退回或拒绝必须填写意见。"})
    if (
        record_action
        in {
            ArticleReviewRecord.Action.FINAL_RETURN,
            ArticleReviewRecord.Action.FINAL_REJECT,
        }
        and not author_visible_comment
    ):
        raise ValidationError(
            {"author_visible_comment": "终审退回或拒绝必须填写作者可见原因。"}
        )
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    existing = _existing_review_result(
        request_id=request_uuid,
        article=locked,
        expected_action=record_action,
    )
    if existing:
        return existing
    revision = _validate_expected(
        article=locked,
        expected_state=expected_state,
        expected_revision_id=expected_revision_id,
        actor=actor,
    )
    if not can_final_review(actor, locked):
        raise PermissionDenied("只有本刊有效主编辑可以执行终审。")
    initial_record = (
        ArticleReviewRecord.objects.filter(
            article=locked,
            stage=ArticleReviewRecord.Stage.INITIAL,
            action=ArticleReviewRecord.Action.INITIAL_APPROVE,
            revision=revision,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if initial_record is None:
        raise ValidationError("当前 revision 没有有效初审通过记录，不能终审。")
    from .category_services import validate_article_category_revision

    validate_article_category_revision(
        article=locked,
        revision_content=revision.content,
        action="final",
    )
    assignment = get_journal_editor_assignment(actor, locked.primary_journal)
    if assignment is None or assignment.journal_id != locked.primary_journal_id:
        raise PermissionDenied("终审任命与文章主属期刊不一致。")
    if record_action == ArticleReviewRecord.Action.FINAL_APPROVE:
        projection = {
            "review_status": ArticlePage.ReviewStatus.APPROVED,
            "approved_version_id": revision.pk,
        }
        if locked.publication_status not in {
            ArticlePage.PublicationStatus.BUILT,
            ArticlePage.PublicationStatus.PUBLISHED,
        }:
            projection["publication_status"] = ArticlePage.PublicationStatus.APPROVED
    elif record_action == ArticleReviewRecord.Action.FINAL_RETURN:
        projection = {
            "review_status": ArticlePage.ReviewStatus.DRAFT,
            "approved_version_id": None,
        }
    else:
        projection = {
            "review_status": ArticlePage.ReviewStatus.REJECTED,
            "rejected_version_id": revision.pk,
            "approved_version_id": None,
        }
        if locked.publication_status:
            projection["publication_status"] = ArticlePage.PublicationStatus.OFFLINE
    if record_action != ArticleReviewRecord.Action.FINAL_APPROVE:
        projection.update(
            assigned_initial_editor_id=None,
            assigned_by_id=None,
            assigned_at=None,
            assignment_request_id=None,
        )
    _write_review_projection(locked, **projection)
    record = ArticleReviewRecord.objects.create(
        article=locked,
        stage=ArticleReviewRecord.Stage.FINAL,
        action=record_action,
        revision=revision,
        reviewer=actor,
        journal_editor_assignment=assignment,
        reviewer_role=assignment.role,
        request_id=request_uuid,
        comment=comment,
        author_visible_comment=author_visible_comment,
    )
    locked.refresh_from_db(
        fields=("review_status", "approved_version", "publication_status")
    )
    if record_action == ArticleReviewRecord.Action.FINAL_APPROVE and (
        locked.review_status != ArticlePage.ReviewStatus.APPROVED
        or locked.approved_version_id != revision.pk
    ):
        raise ArticleStateConflict("终审记录与文章批准状态未能原子同步。")
    from .publication import sync_article_placement_status

    sync_article_placement_status(locked.pk)
    _record_audit(
        actor=actor,
        article=locked,
        record=record,
        message="完成文章终审动作。",
        comment=comment,
        old_state=ArticlePage.ReviewStatus.PENDING_FINAL,
        new_state=locked.review_status,
    )
    return record


@transaction.atomic
def reopen_rejected_article(
    *,
    actor,
    article,
    reason,
    expected_state,
    expected_revision_id,
    request_id,
):
    request_uuid = _request_uuid(request_id)
    existing = _existing_review_result(
        request_id=request_uuid,
        article=article,
        expected_action=ArticleReviewRecord.Action.REOPEN,
    )
    if existing:
        return existing
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "重新开启拒绝文章必须填写原因。"})
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    existing = _existing_review_result(
        request_id=request_uuid,
        article=locked,
        expected_action=ArticleReviewRecord.Action.REOPEN,
    )
    if existing:
        return existing
    assignment = get_journal_editor_assignment(actor, locked.primary_journal)
    if not is_super_admin(actor) and not (
        assignment and assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR
    ):
        raise PermissionDenied("只有本刊主编辑或超级管理员可以重新开启文章。")
    if expected_state and locked.review_status != expected_state:
        raise ArticleStateConflict(
            f"文章状态已从 {expected_state} 变为 {locked.review_status}，请刷新后重试。"
        )
    if locked.review_status != ArticlePage.ReviewStatus.REJECTED:
        raise ArticleStateConflict("只有已拒绝文章可以重新开启。")
    current_revision = _latest_revision(locked, actor)
    if str(current_revision.pk) != str(expected_revision_id or ""):
        raise ArticleRevisionConflict("文章 revision 已变化，请刷新后重试。")
    locked.review_status = ArticlePage.ReviewStatus.DRAFT
    locked.assigned_initial_editor = None
    locked.assigned_by = None
    locked.assigned_at = None
    locked.assignment_request_id = None
    locked.rejected_version = None
    locked.save(bypass_article_permission_check=True)
    revision = locked.save_revision(
        user=actor,
        changed=True,
        bypass_article_permission_check=True,
    )
    record = ArticleReviewRecord.objects.create(
        article=locked,
        stage=ArticleReviewRecord.Stage.INITIAL,
        action=ArticleReviewRecord.Action.REOPEN,
        revision=revision,
        reviewer=actor,
        journal_editor_assignment=assignment,
        reviewer_role=_role_snapshot(actor, assignment),
        request_id=request_uuid,
        comment=reason,
    )
    _record_audit(
        actor=actor,
        article=locked,
        record=record,
        message="重新开启已拒绝文章。",
        comment=reason,
        old_state=ArticlePage.ReviewStatus.REJECTED,
        new_state=locked.review_status,
    )
    return record


def has_valid_final_approval(article, revision) -> bool:
    if article is None or revision is None:
        return False
    if (
        article.review_status
        not in {
            ArticlePage.ReviewStatus.APPROVED,
            ArticlePage.ReviewStatus.PUBLISHED,
        }
        or article.approved_version_id != revision.pk
    ):
        return False
    return ArticleReviewRecord.objects.filter(
        article=article,
        stage=ArticleReviewRecord.Stage.FINAL,
        action=ArticleReviewRecord.Action.FINAL_APPROVE,
        revision=revision,
    ).exists()
