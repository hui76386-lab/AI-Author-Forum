from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionRule:
    """A reusable permission rule made of mandatory and alternative permissions."""

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()


ADMIN_ACCESS_PERMISSION = "wagtailadmin.access_admin"

# These groups are the project-level administrators defined by the business
# permission baseline. They intentionally receive the same application-level
# bypass as Django superusers, while remaining ordinary Wagtail users so their
# membership and actions stay visible in the audit trail.
GLOBAL_ADMIN_GROUP_NAMES = frozenset({"项目总负责人", "超级管理员"})


def is_global_admin(user) -> bool:
    """Return whether ``user`` has project-wide administrator privileges.

    The project roles are represented by Wagtail/Django groups, not only by
    ``User.is_superuser``. Any code that implements a project-level permission
    bypass must use this helper so the role definition behaves consistently.
    """

    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    cached = getattr(user, "_is_global_admin", None)
    if cached is not None:
        return cached

    groups = getattr(user, "groups", None)
    if groups is None:
        return False
    result = groups.filter(name__in=GLOBAL_ADMIN_GROUP_NAMES).exists()
    user._is_global_admin = result
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

    flags = {
        name: evaluate_permission_rule(user, rule)
        for name, rule in PERMISSION_RULES.items()
    }
    flags["has_write_capability"] = any(flags[name] for name in WRITE_PERMISSION_FLAGS)
    flags["has_dashboard_access"] = flags["has_write_capability"] or any(
        flags[name] for name in READ_PERMISSION_FLAGS
    )
    flags["is_readonly_dashboard"] = (
        flags["has_dashboard_access"] and not flags["has_write_capability"]
    )
    return flags
