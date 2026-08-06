from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import Exists, OuterRef
from wagtail.models import GroupCollectionPermission, GroupPagePermission

from ai_author_forum.articles.models import (
    ArticlePage,
    ArticleReviewRecord,
    ArticleReviewTask,
)
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

LEGACY_GROUP_NAMES = (
    "项目总负责人",
    "内容管理员",
    "审核人员",
    "站点运营",
    "发布管理员",
    "只读人员",
    "内容编辑",
    "内容审核",
    "内容发布",
    "Content Editors",
    "Content Reviewers",
    "Content Publishers",
)


def load_mapping(path):
    if not path:
        return {"super_admins": [], "assignments": [], "deactivate_users": []}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mapping root must be a JSON object.")
    payload.setdefault("super_admins", [])
    payload.setdefault("assignments", [])
    payload.setdefault("deactivate_users", [])
    return payload


def _user(identifier):
    User = get_user_model()
    return (
        User.objects.filter(username=identifier).first()
        or User.objects.filter(email__iexact=identifier).first()
    )


def validate_mapping(mapping):
    errors = []
    warnings = []
    super_identifiers = set(mapping.get("super_admins") or [])
    assignments = mapping.get("assignments") or []
    seen = set()
    mapped_chiefs = {}
    for identifier in super_identifiers:
        if _user(identifier) is None:
            errors.append(f"Unknown super administrator account: {identifier}")
    for index, row in enumerate(assignments, start=1):
        if not isinstance(row, dict):
            errors.append(f"assignments[{index}] must be an object")
            continue
        identifier = row.get("user", "")
        journal_slug = row.get("journal", "")
        role = row.get("role", "")
        user = _user(identifier)
        journal = Journal.objects.filter(slug=journal_slug).first()
        if user is None:
            errors.append(f"assignments[{index}] has unknown user: {identifier}")
        if journal is None:
            errors.append(f"assignments[{index}] has unknown journal: {journal_slug}")
        if role not in JournalEditorAssignment.Role.values:
            errors.append(f"assignments[{index}] has invalid role: {role}")
        responsibilities = row.get("responsibilities") or []
        invalid = set(responsibilities) - set(
            JournalEditorAssignment.ALL_RESPONSIBILITIES
        )
        if invalid:
            errors.append(
                f"assignments[{index}] has invalid responsibilities: {sorted(invalid)}"
            )
        if (
            role == JournalEditorAssignment.Role.ASSOCIATE_EDITOR
            and not responsibilities
        ):
            errors.append(
                f"assignments[{index}] associate editor needs responsibilities"
            )
        key = (identifier, journal_slug, role)
        if key in seen:
            errors.append(f"Duplicate assignment mapping: {key}")
        seen.add(key)
        if identifier in super_identifiers:
            errors.append(
                f"Account cannot be both super administrator and journal editor: {identifier}"
            )
        if role == JournalEditorAssignment.Role.CHIEF_EDITOR and journal:
            mapped_chiefs.setdefault(journal.pk, []).append(identifier)

    chief_validation = []
    for journal in Journal.objects.filter(status=JournalStatus.ACTIVE).order_by("slug"):
        mapped = mapped_chiefs.get(journal.pk)
        if mapped is None:
            candidates = list(
                JournalEditorAssignment.objects.effective()
                .filter(
                    journal=journal,
                    role=JournalEditorAssignment.Role.CHIEF_EDITOR,
                )
                .values_list("user__username", flat=True)
            )
        else:
            candidates = mapped
        valid = len(candidates) == 1
        chief_validation.append(
            {
                "journal": journal.slug,
                "candidate_chiefs": candidates,
                "valid": valid,
            }
        )
        if not valid:
            errors.append(
                f"Active journal {journal.slug} must resolve to exactly one chief editor"
            )

    for identifier in mapping.get("deactivate_users") or []:
        if _user(identifier) is None:
            errors.append(f"Unknown account to deactivate: {identifier}")
        if identifier in super_identifiers:
            errors.append(f"Account cannot be mapped and deactivated: {identifier}")
    if not assignments:
        warnings.append(
            "No journal assignments were supplied; no journal scope is inferred."
        )
    return {
        "errors": errors,
        "warnings": warnings,
        "chief_validation": chief_validation,
    }


def build_report(mapping=None):
    mapping = mapping or {"super_admins": [], "assignments": [], "deactivate_users": []}
    User = get_user_model()
    final_records = ArticleReviewRecord.objects.filter(
        article_id=OuterRef("pk"),
        stage=ArticleReviewRecord.Stage.FINAL,
        action=ArticleReviewRecord.Action.FINAL_APPROVE,
        revision_id=OuterRef("approved_version_id"),
    )
    unverified = (
        ArticlePage.objects.filter(
            review_status__in=(
                ArticlePage.ReviewStatus.APPROVED,
                ArticlePage.ReviewStatus.PUBLISHED,
            )
        )
        .annotate(has_final_source=Exists(final_records))
        .filter(has_final_source=False)
        .values("pk", "title", "primary_journal__slug", "approved_version_id")
    )
    users = []
    for user in User.objects.prefetch_related("groups", "user_permissions").order_by(
        "pk"
    ):
        users.append(
            {
                "id": user.pk,
                "username": user.username,
                "email": user.email,
                "display_name": user.display_name,
                "account_status": user.account_status,
                "is_active": user.is_active,
                "is_superuser_recovery_flag": user.is_superuser,
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "groups": sorted(user.groups.values_list("name", flat=True)),
                "direct_permissions": sorted(
                    f"{perm.content_type.app_label}.{perm.codename}"
                    for perm in user.user_permissions.select_related("content_type")
                ),
            }
        )
    legacy_groups = []
    for group in Group.objects.filter(name__in=LEGACY_GROUP_NAMES).order_by("name"):
        model_permissions = sorted(
            f"{permission.content_type.app_label}.{permission.codename}"
            for permission in group.permissions.select_related("content_type")
        )
        page_permissions = sorted(
            f"{item.page_id}:{item.permission.content_type.app_label}.{item.permission.codename}"
            for item in GroupPagePermission.objects.filter(group=group).select_related(
                "permission__content_type"
            )
        )
        collection_permissions = sorted(
            f"{item.collection_id}:{item.permission.content_type.app_label}.{item.permission.codename}"
            for item in GroupCollectionPermission.objects.filter(
                group=group
            ).select_related("permission__content_type")
        )
        legacy_groups.append(
            {
                "name": group.name,
                "members": sorted(group.user_set.values_list("username", flat=True)),
                "permission_count": len(model_permissions),
                "permissions": model_permissions,
                "page_permissions": page_permissions,
                "collection_permissions": collection_permissions,
            }
        )
    validation = validate_mapping(mapping)
    review_permission = Permission.objects.filter(
        content_type__app_label="articles",
        codename="review_article",
    ).first()
    direct_review_permission_users = []
    if review_permission is not None:
        direct_review_permission_users = sorted(
            review_permission.user_set.values_list("username", flat=True)
        )
    return {
        "policy": {
            "automatic_all_journal_grants": False,
            "super_admin_group": SUPER_ADMIN_GROUP_NAME,
            "legacy_groups_retained_without_business_authority": True,
        },
        "users": users,
        "legacy_groups": legacy_groups,
        "legacy_project_lead_members": sorted(
            Group.objects.filter(name="项目总负责人", user__isnull=False).values_list(
                "user__username", flat=True
            )
        ),
        "direct_review_permission_users": direct_review_permission_users,
        "legacy_single_stage_review_tasks": list(
            ArticleReviewTask.objects.order_by("pk").values("pk", "name", "active")
        ),
        "pending_review_articles": list(
            ArticlePage.objects.filter(
                review_status__in=(
                    ArticlePage.ReviewStatus.SUBMITTED,
                    ArticlePage.ReviewStatus.PENDING_FINAL,
                )
            )
            .order_by("pk")
            .values(
                "pk",
                "title",
                "primary_journal__slug",
                "review_status",
                "latest_revision_created_at",
                "approved_version_id",
            )
        ),
        "legacy_approved_without_final_source": list(unverified),
        "mapping_validation": validation,
    }
