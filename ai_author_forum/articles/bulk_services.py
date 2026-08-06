from __future__ import annotations

from dataclasses import asdict, dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from ai_author_forum.journals.models import JournalEditorAssignment
from ai_author_forum.site_settings.access_control import can_manage_journal

from .integrations import log_article_audit
from .models import (
    ArticleCategoryAssignment,
    ArticlePage,
    ArticleRevisionConflict,
    user_has_article_edit_permission,
    user_has_article_review_permission,
)

MAX_BULK_ARTICLES = 100
EDIT_ACTIONS = {
    "submit_review",
    "set_primary_journal",
    "set_primary_category",
    "add_category",
    "set_article_type",
}
REVIEW_ACTIONS = {"approve", "reject"}
SUPPORTED_ACTIONS = EDIT_ACTIONS | REVIEW_ACTIONS


@dataclass
class BulkItemResult:
    article_id: int
    title: str
    success: bool
    message: str


@dataclass
class BulkActionResult:
    action: str
    selected_count: int
    success_count: int
    failure_count: int
    items: list[BulkItemResult]

    def as_dict(self):
        payload = asdict(self)
        payload["items"] = [asdict(item) for item in self.items]
        return payload


def user_can_bulk_action(user, action):
    if action in EDIT_ACTIONS:
        return user_has_article_edit_permission(user)
    if action in REVIEW_ACTIONS:
        return user_has_article_review_permission(user)
    return False


@transaction.atomic
def execute_bulk_article_action(
    *,
    user,
    article_ids,
    action,
    params=None,
    comment="",
    expected_revisions=None,
):
    params = params or {}
    expected_revisions = expected_revisions or {}
    ids = _normalise_ids(article_ids)
    if action not in SUPPORTED_ACTIONS:
        raise ValidationError("不支持的批量操作。")
    if not ids:
        raise ValidationError("请至少选择一篇文章。")
    if len(ids) > MAX_BULK_ARTICLES:
        raise ValidationError("一次最多处理 100 篇文章。")
    if not user_can_bulk_action(user, action):
        raise PermissionDenied("您没有执行此批量操作的权限。")
    if action in REVIEW_ACTIONS and not str(comment or "").strip():
        raise ValidationError("批量批准或驳回必须填写审核意见。")

    found = {
        article.pk: article
        for article in ArticlePage.objects.filter(pk__in=ids).select_related(
            "primary_journal", "latest_revision"
        )
    }
    items = []
    for article_id in ids:
        article = found.get(article_id)
        if article is None:
            items.append(BulkItemResult(article_id, "", False, "文章不存在或已删除。"))
            continue
        try:
            with transaction.atomic():
                _execute_one(
                    article=article,
                    user=user,
                    action=action,
                    params=params,
                    comment=comment,
                    expected_revision_id=expected_revisions.get(str(article_id))
                    or expected_revisions.get(article_id),
                )
        except (PermissionDenied, ValidationError, ArticleRevisionConflict) as exc:
            items.append(
                BulkItemResult(
                    article_id, article.title, False, _exception_message(exc)
                )
            )
        except Exception as exc:  # keep partial success and expose a safe item reason
            items.append(
                BulkItemResult(
                    article_id,
                    article.title,
                    False,
                    f"处理失败：{exc}",
                )
            )
        else:
            items.append(BulkItemResult(article_id, article.title, True, "处理成功"))

    success_count = sum(item.success for item in items)
    result = BulkActionResult(
        action=action,
        selected_count=len(ids),
        success_count=success_count,
        failure_count=len(ids) - success_count,
        items=items,
    )
    audit_target = next((found[item_id] for item_id in ids if item_id in found), None)
    if audit_target is not None:
        log_article_audit(
            action=f"bulk_{action}",
            article=audit_target,
            user=user,
            comment=str(comment or ""),
            metadata={
                "batch": True,
                "action": action,
                "selected_count": result.selected_count,
                "success_count": result.success_count,
                "failure_count": result.failure_count,
                "article_ids": ids,
                "failed": [
                    {"article_id": item.article_id, "reason": item.message}
                    for item in items
                    if not item.success
                ],
            },
        )
    return result


def _execute_one(*, article, user, action, params, comment, expected_revision_id):
    locked = ArticlePage.objects.select_for_update().get(pk=article.pk)
    expected_revision_id = _check_expected_revision(
        locked,
        expected_revision_id,
        required=action in REVIEW_ACTIONS,
    )

    if action in EDIT_ACTIONS:
        locked._raise_if_user_cannot_save(user)
    if action == "submit_review":
        if locked.review_status not in {
            ArticlePage.ReviewStatus.DRAFT,
            ArticlePage.ReviewStatus.REJECTED,
        }:
            raise ValidationError("仅草稿或已驳回文章可提交审核。")
        locked.submit_for_review(
            user,
            comment,
            expected_revision_id=expected_revision_id,
        )
        return
    if action == "approve":
        _require_submitted(locked)
        locked.approve(user, comment, expected_revision_id=expected_revision_id)
        return
    if action == "reject":
        _require_submitted(locked)
        locked.reject(user, comment, expected_revision_id=expected_revision_id)
        return
    if action == "set_primary_journal":
        _set_primary_journal(locked, params.get("primary_journal"), user=user)
    elif action == "set_primary_category":
        _set_primary_category(locked, params.get("category"))
    elif action == "add_category":
        _add_category(locked, params.get("category"))
    elif action == "set_article_type":
        _set_article_type(locked, params.get("article_type"))
    _save_content_change(locked, user)


def _set_primary_journal(article, value, *, user):
    journal_model = ArticlePage._meta.get_field("primary_journal").remote_field.model
    try:
        journal = journal_model.objects.get(pk=int(value))
    except (TypeError, ValueError, journal_model.DoesNotExist):
        raise ValidationError("请选择有效的主属期刊。") from None
    if not can_manage_journal(
        user,
        journal,
        JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
    ):
        raise PermissionDenied("无权将文章移动到该子期刊。")
    incompatible = article.category_assignments.exclude(category__journal=journal)
    if incompatible.exists():
        raise ValidationError("现有动态栏目不属于目标期刊，请先调整栏目。")
    article.primary_journal = journal


def _get_assignable_category(article, value):
    category_model = ArticleCategoryAssignment._meta.get_field(
        "category"
    ).remote_field.model
    try:
        category = category_model.objects.get(pk=int(value))
    except (TypeError, ValueError, category_model.DoesNotExist):
        raise ValidationError("请选择有效的动态栏目") from None

    assignment = ArticleCategoryAssignment(article=article, category=category)
    assignment.full_clean(exclude={"id", "sort_order"})
    return category


def _set_primary_category(article, value):
    category = _get_assignable_category(article, value)
    ArticleCategoryAssignment.objects.filter(article=article, is_primary=True).update(
        is_primary=False
    )
    assignment, created = ArticleCategoryAssignment.objects.get_or_create(
        article=article,
        category=category,
        defaults={"is_primary": True},
    )
    if not created and not assignment.is_primary:
        assignment.is_primary = True
        assignment.save(update_fields=["is_primary"])


def _add_category(article, value):
    category = _get_assignable_category(article, value)
    ArticleCategoryAssignment.objects.get_or_create(
        article=article,
        category=category,
        defaults={"is_primary": not article.category_assignments.exists()},
    )


def _set_article_type(article, value):
    if value not in ArticlePage.ArticleType.values:
        raise ValidationError("请选择有效的文章类型。")
    article.article_type = value


def _save_content_change(article, user):
    article.save(user=user)
    article.save_revision(user=user, log_action=True)


def _check_expected_revision(article, expected_revision_id, *, required=False):
    if expected_revision_id in (None, ""):
        if required:
            raise ValidationError("批量审核必须提供 expected revision。")
        return None

    if isinstance(expected_revision_id, bool):
        raise ValidationError("expected revision 非法，请刷新列表后重试。")
    try:
        expected_revision_id = int(expected_revision_id)
    except (TypeError, ValueError):
        raise ValidationError("expected revision 非法，请刷新列表后重试。") from None
    if expected_revision_id <= 0:
        raise ValidationError("expected revision 非法，请刷新列表后重试。")

    latest = article.get_latest_revision()
    latest_id = latest.pk if latest else None
    if latest_id != expected_revision_id:
        raise ArticleRevisionConflict("文章 revision 已变化，请刷新列表后重试。")
    return expected_revision_id


def _require_submitted(article):
    if article.review_status != ArticlePage.ReviewStatus.SUBMITTED:
        raise ValidationError("文章已不处于待审核状态。")


def _normalise_ids(values):
    ids = []
    seen = set()
    for value in values:
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in seen:
            ids.append(value)
            seen.add(value)
    return ids


def _exception_message(exc):
    if hasattr(exc, "messages") and exc.messages:
        return "；".join(str(item) for item in exc.messages)
    return str(exc)
