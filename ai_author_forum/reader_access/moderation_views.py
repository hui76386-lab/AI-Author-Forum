"""Wagtail moderation workspace with journal-scoped query and command checks."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from ai_author_forum.journals.models import Journal
from ai_author_forum.reader_interactions.models import Comment, CommentReport

from .moderation import create_moderation_command
from .permissions import accessible_journals, can_manage_policy


def _moderator_journal_ids(user):
    return list(accessible_journals(user).values_list("pk", flat=True))


def moderation_index(request):
    journal_ids = _moderator_journal_ids(request.user)
    comments = (
        Comment.objects.using("interactions")
        .filter(journal_id__in=journal_ids)
        .select_related("reader")
        .order_by("created_at", "public_id")
    )
    status = request.GET.get("status", "pending")
    if status in Comment.State.values:
        comments = comments.filter(state=status)
    elif status != "all":
        status = "pending"
        comments = comments.filter(state=Comment.State.PENDING)
    reports = (
        CommentReport.objects.using("interactions")
        .filter(comment__journal_id__in=journal_ids, status=CommentReport.Status.OPEN)
        .select_related("comment", "reporter")
        .order_by("created_at")[:200]
    )
    journals = Journal.objects.filter(pk__in=journal_ids).order_by("name")
    return render(
        request,
        "wagtailadmin/reader_access/moderation_index.html",
        {
            "title": "读者评论审核",
            "comments": comments[:500],
            "reports": reports,
            "journals": journals,
            "status": status,
        },
    )


@csrf_protect
@require_http_methods(["POST"])
def moderation_action(request, comment_public_id):
    try:
        comment_id = UUID(str(comment_public_id))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid comment id.") from exc
    comment = get_object_or_404(
        Comment.objects.using("interactions"), public_id=comment_id
    )
    journal = get_object_or_404(Journal, pk=comment.journal_id)
    if not can_manage_policy(request.user, journal):
        raise PermissionDenied("无权审核该期刊评论。")
    try:
        result = create_moderation_command(
            actor=request.user,
            comment_public_id=comment.public_id,
            action=request.POST.get("action", ""),
            expected_version=int(request.POST.get("expected_version", comment.version)),
            reason=request.POST.get("reason", ""),
            note=request.POST.get("note", ""),
            idempotency_key=request.POST.get("idempotency_key", "")
            or request.headers.get("Idempotency-Key", ""),
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        if result.status == "unknown":
            messages.warning(request, "审核结果未知，已进入对账队列。")
        else:
            messages.success(request, "审核命令已提交，结果将在跨库确认后显示。")
    return redirect("reader_access_moderation_index")


@csrf_protect
@require_http_methods(["POST"])
def moderation_batch(request):
    ids = [
        value.strip()
        for value in request.POST.get("comment_ids", "").split(",")
        if value.strip()
    ]
    action = request.POST.get("action", "")
    rows = []
    for value in ids[:100]:
        comment = Comment.objects.using("interactions").filter(public_id=value).first()
        if comment is None:
            rows.append({"comment_id": value, "status": "failed"})
            continue
        rows.append(
            {
                "comment_public_id": comment.public_id,
                "action": action,
                "expected_version": comment.version,
                "reason": request.POST.get("reason", ""),
                "note": request.POST.get("note", ""),
                "idempotency_key": f"batch:{request.user.pk}:{comment.public_id}",
            }
        )
    from .moderation import batch_moderate_comments

    results = batch_moderate_comments(actor=request.user, items=rows)
    failed = sum(1 for row in results if row.get("status") == "failed")
    messages.warning(
        request, f"批量审核完成：{len(results) - failed} 成功提交，{failed} 项失败。"
    )
    return redirect("reader_access_moderation_index")
