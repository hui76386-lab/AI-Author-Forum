from __future__ import annotations

from dataclasses import dataclass

from ai_author_forum.journals.models import JournalEditorAssignment
from ai_author_forum.site_settings.access_control import (
    is_super_admin,
)


@dataclass(frozen=True)
class PermissionRule:
    """A reusable permission rule made of mandatory and alternative permissions."""

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()


ADMIN_ACCESS_PERMISSION = "wagtailadmin.access_admin"

GLOBAL_ADMIN_GROUP_NAMES = frozenset({"超级管理员"})


def is_global_admin(user) -> bool:
    """Return whether ``user`` has project-wide administrator privileges.

    This compatibility entry point delegates to the sole platform business role.
    Django ``is_superuser`` remains a recovery mechanism and is not an ordinary
    business authorization bypass.
    """

    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    cached = getattr(user, "_is_super_admin", None)
    if cached is not None:
        return cached

    groups = getattr(user, "groups", None)
    if groups is None:
        return False
    result = groups.filter(name="超级管理员").exists()
    user._is_super_admin = result
    return result


PERMISSION_RULES: dict[str, PermissionRule] = {
    "can_add_journal": PermissionRule(
        all_of=(ADMIN_ACCESS_PERMISSION, "journals.add_journal"),
    ),
    "can_change_journal": PermissionRule(
        all_of=(ADMIN_ACCESS_PERMISSION, "journals.change_journal"),
    ),
    "can_import_journals": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "journals.add_journal",
            "site_settings.import_journals",
        ),
    ),
    "can_import_articles": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_articles",
            "site_settings.import_articles",
            "articles.edit_article",
        ),
    ),
    "can_edit_article": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "articles.view_articlepage",
            "articles.edit_article",
        ),
    ),
    "can_review_article": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "articles.view_articlepage",
            "articles.review_article",
        ),
    ),
    "can_manage_placement": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "placements.manage_manual_categoryplacement",
        ),
        any_of=(
            "placements.add_articleplacement",
            "placements.change_articleplacement",
        ),
    ),
    "can_publish_static": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "static_publish.view_staticpublishjob",
            "static_publish.publish_static_site",
        ),
    ),
    "can_retry_publish": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "static_publish.view_staticpublishjob",
            "static_publish.retry_category_publish",
        ),
    ),
    "can_rollback_publish": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "static_publish.view_staticpublishjob",
            "static_publish.rollback_category_publish",
        ),
    ),
    # Read capabilities are intentionally based on both module access and model
    # permissions, so a dashboard link never exposes data from an inaccessible page.
    "can_view_articles": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_articles",
            "articles.view_articlepage",
        ),
    ),
    "can_view_article_review": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_article_review",
            "articles.view_articlepage",
        ),
    ),
    "can_view_journals": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_journals",
            "journals.view_journal",
        ),
    ),
    "can_view_journal_categories": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "journals.view_journalcategory",
        ),
    ),
    "can_view_placements": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_placements",
            "placements.view_articleplacement",
        ),
    ),
    "can_view_slots": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_slots",
            "placements.view_layoutslot",
        ),
    ),
    "can_view_static_publish": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_static_publish",
            "static_publish.view_staticpublishjob",
        ),
    ),
    "can_view_audit_log": PermissionRule(
        all_of=(
            ADMIN_ACCESS_PERMISSION,
            "site_settings.access_audit_log",
            "site_settings.view_auditlog",
        ),
    ),
}

WRITE_PERMISSION_FLAGS = (
    "can_add_journal",
    "can_change_journal",
    "can_import_journals",
    "can_import_articles",
    "can_edit_article",
    "can_review_article",
    "can_manage_placement",
    "can_publish_static",
    "can_retry_publish",
    "can_rollback_publish",
)

READ_PERMISSION_FLAGS = (
    "can_view_articles",
    "can_view_article_review",
    "can_view_journals",
    "can_view_journal_categories",
    "can_view_placements",
    "can_view_slots",
    "can_view_static_publish",
    "can_view_audit_log",
)


def _permission_set(user) -> set[str]:
    cached = getattr(user, "_admin_maturity_permissions", None)
    if cached is None:
        cached = set(user.get_all_permissions())
        user._admin_maturity_permissions = cached
    return cached


def evaluate_permission_rule(user, rule: PermissionRule) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    if is_global_admin(user):
        return True

    permissions = _permission_set(user)
    if not all(permission in permissions for permission in rule.all_of):
        return False
    return not rule.any_of or any(
        permission in permissions for permission in rule.any_of
    )


def get_admin_permission_context(user) -> dict[str, bool]:
    """Return the canonical fine-grained admin permission flags for ``user``."""
    if is_super_admin(user):
        flags = {name: True for name in PERMISSION_RULES}
        flags.update(
            {
                "can_add_placement": True,
                "can_republish_placement": True,
                "can_manage_journal_profile": True,
                "can_manage_journal_categories": True,
                "can_manage_issues": True,
                "can_manage_media_assets": True,
                "can_manage_editorial_team": True,
            }
        )
    else:
        assignments = JournalEditorAssignment.objects.effective().filter(user=user)
        has_assignment = assignments.exists()
        article_maintenance = assignments.filter(
            role__in=(
                JournalEditorAssignment.Role.CHIEF_EDITOR,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
            )
        ).exists() or any(
            JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
            in (responsibilities or [])
            for responsibilities in assignments.values_list(
                "responsibilities", flat=True
            )
        )
        add_placement = assignments.filter(
            role=JournalEditorAssignment.Role.CHIEF_EDITOR
        ).exists()
        placement = article_maintenance

        def has_responsibility(code):
            return any(
                code in (responsibilities or [])
                for responsibilities in assignments.values_list(
                    "responsibilities", flat=True
                )
            )

        journal_profile = has_responsibility(
            JournalEditorAssignment.Responsibility.JOURNAL_PROFILE
        )
        column_navigation = has_responsibility(
            JournalEditorAssignment.Responsibility.COLUMN_NAVIGATION
        )
        issue_management = has_responsibility(
            JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT
        )
        media_assets = has_responsibility(
            JournalEditorAssignment.Responsibility.MEDIA_ASSETS
        )
        flags = {name: False for name in PERMISSION_RULES}
        flags.update(
            {
                "can_edit_article": article_maintenance,
                "can_import_articles": article_maintenance,
                "can_review_article": has_assignment,
                "can_manage_placement": placement,
                "can_add_placement": add_placement,
                "can_republish_placement": placement,
                "can_view_articles": has_assignment,
                "can_view_article_review": has_assignment,
                "can_view_journals": has_assignment,
                "can_view_journal_categories": column_navigation,
                "can_view_placements": placement,
                "can_view_audit_log": has_assignment,
                "can_manage_journal_profile": journal_profile,
                "can_manage_journal_categories": column_navigation,
                "can_manage_issues": issue_management,
                "can_manage_media_assets": media_assets,
                "can_manage_editorial_team": has_assignment,
            }
        )
    flags["has_write_capability"] = any(flags[name] for name in WRITE_PERMISSION_FLAGS)
    flags["has_dashboard_access"] = flags["has_write_capability"] or any(
        flags[name] for name in READ_PERMISSION_FLAGS
    )
    flags["is_readonly_dashboard"] = (
        flags["has_dashboard_access"] and not flags["has_write_capability"]
    )
    return flags
