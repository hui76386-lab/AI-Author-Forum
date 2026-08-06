from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from wagtail.models import (
    Collection,
    GroupCollectionPermission,
    GroupPagePermission,
    Page,
)

from ai_author_forum.site_settings.models import AdminRolePreset

ROLE_DEFINITIONS = {
    "super_admin": {
        "display_name": "超级管理员",
        "description": "管理全局配置、用户权限、发布和回滚。",
        "custom_permissions": "*",
        "page_permissions": {
            "add_page",
            "bulk_delete_page",
            "change_page",
            "delete_page",
            "lock_page",
            "publish_page",
            "unlock_page",
            "view_page",
        },
        "collection_access": {
            "wagtailimages": {
                "add_image",
                "change_image",
                "choose_image",
                "delete_image",
            },
            "wagtaildocs": {
                "add_document",
                "change_document",
                "choose_document",
                "delete_document",
            },
        },
    }
}

JOURNAL_EDITOR_ACCESS_GROUP_NAME = "子期刊编辑基础访问"
JOURNAL_EDITOR_CUSTOM_PERMISSIONS = {
    "access_journals",
    "access_articles",
    "access_article_review",
    "access_placements",
}
JOURNAL_EDITOR_VIEW_MODELS = {
    "journals.JournalEditorAssignment": {"view"},
    "journals.JournalCategory": {"view"},
    "articles.ArticlePage": {"view"},
    "articles.ArticleReviewRecord": {"view"},
    "placements.ArticlePlacement": {"view"},
    "placements.LayoutSlot": {"view"},
    "site_settings.AuditLog": {"view"},
}


class Command(BaseCommand):
    help = "Create or update the simple journal RBAC groups."

    def handle(self, *args, **options):
        content_type = ContentType.objects.get_for_model(AdminRolePreset)
        all_permissions = Permission.objects.all()
        AdminRolePreset.objects.exclude(role_code__in=ROLE_DEFINITIONS).update(
            is_active=False
        )
        for role_code, definition in ROLE_DEFINITIONS.items():
            group, _ = Group.objects.get_or_create(name=definition["display_name"])
            group.permissions.clear()
            GroupPagePermission.objects.filter(group=group).delete()
            GroupCollectionPermission.objects.filter(group=group).delete()
            self._assign_admin_access(group)
            self._assign_custom_permissions(
                group,
                content_type,
                definition["custom_permissions"],
                all_permissions,
            )
            self._assign_explicit_permissions(
                group, definition.get("explicit_permissions", set())
            )
            self._assign_model_permissions(group, definition.get("model_access", {}))
            self._assign_page_permissions(
                group, definition.get("page_permissions", set())
            )
            self._assign_collection_permissions(
                group, definition.get("collection_access", {})
            )
            AdminRolePreset.objects.update_or_create(
                role_code=role_code,
                defaults={
                    "display_name": definition["display_name"],
                    "description": definition["description"],
                    "group": group,
                    "is_active": True,
                    "is_system": True,
                },
            )
            self.stdout.write(f"Synchronized role: {definition['display_name']}")

        editor_group, _ = Group.objects.get_or_create(
            name=JOURNAL_EDITOR_ACCESS_GROUP_NAME
        )
        editor_group.permissions.clear()
        GroupPagePermission.objects.filter(group=editor_group).delete()
        GroupCollectionPermission.objects.filter(group=editor_group).delete()
        self._assign_admin_access(editor_group)
        self._assign_custom_permissions(
            editor_group,
            content_type,
            JOURNAL_EDITOR_CUSTOM_PERMISSIONS,
            all_permissions,
        )
        self._assign_model_permissions(editor_group, JOURNAL_EDITOR_VIEW_MODELS)
        # Wagtail checks the parent page before it knows the new page type.
        # before_create_page restricts this grant to in-scope ArticlePage records.
        self._assign_page_permissions(editor_group, {"add_page"})
        self.stdout.write(
            f"Synchronized technical group: {JOURNAL_EDITOR_ACCESS_GROUP_NAME}"
        )
        self.stdout.write(self.style.SUCCESS("Role groups synchronized."))

    def _assign_admin_access(self, group: Group) -> None:
        permission = Permission.objects.get(
            content_type__app_label="wagtailadmin",
            codename="access_admin",
        )
        group.permissions.add(permission)

    def _assign_custom_permissions(
        self,
        group: Group,
        content_type: ContentType,
        codenames: str | Iterable[str],
        all_permissions,
    ) -> None:
        if codenames == "*":
            group.permissions.add(*all_permissions)
            return
        permissions = Permission.objects.filter(
            content_type=content_type,
            codename__in=list(codenames),
        )
        group.permissions.add(*permissions)

    def _assign_explicit_permissions(
        self, group: Group, permission_names: Iterable[str]
    ) -> None:
        for permission_name in permission_names:
            app_label, codename = permission_name.split(".", 1)
            permission = Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
            group.permissions.add(permission)

    def _assign_model_permissions(
        self, group: Group, access_by_app: dict[str, set[str]]
    ) -> None:
        for resource, actions in access_by_app.items():
            if "." in resource:
                app_label, model_name = resource.split(".", 1)
                content_types = ContentType.objects.filter(
                    app_label=app_label,
                    model=model_name.lower(),
                )
            else:
                content_types = ContentType.objects.filter(app_label=resource)
            permissions = Permission.objects.filter(
                content_type__in=content_types,
                codename__in={
                    f"{action}_{content_type.model}"
                    for content_type in content_types
                    for action in actions
                },
            )
            group.permissions.add(*permissions)

    def _assign_page_permissions(self, group: Group, codenames: set[str]) -> None:
        if not codenames:
            return
        try:
            root_page = Page.get_first_root_node()
        except Page.DoesNotExist:
            return
        page_content_type = ContentType.objects.get_for_model(Page)
        permissions = Permission.objects.filter(
            content_type=page_content_type,
            codename__in=codenames,
        )
        for permission in permissions:
            GroupPagePermission.objects.get_or_create(
                group=group,
                page=root_page,
                permission=permission,
            )

    def _assign_collection_permissions(
        self, group: Group, access_by_app: dict[str, set[str]]
    ) -> None:
        if not access_by_app:
            return
        root_collection = Collection.get_first_root_node()
        for app_label, codenames in access_by_app.items():
            permissions = Permission.objects.filter(
                content_type__app_label=app_label,
                codename__in=codenames,
            )
            for permission in permissions:
                GroupCollectionPermission.objects.get_or_create(
                    group=group,
                    collection=root_collection,
                    permission=permission,
                )
