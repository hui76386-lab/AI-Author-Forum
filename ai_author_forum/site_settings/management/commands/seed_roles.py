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
    "project_lead": {
        "display_name": "项目总负责人",
        "description": "拥有全部代码、配置、权限、审核、发布、回滚和部署决策权限。",
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
    },
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
    },
    "content_manager": {
        "display_name": "内容管理员",
        "description": "负责文章、栏目、人工投放和预览；不能移动、停用或归档栏目。",
        "custom_permissions": {
            "access_articles",
            "access_placements",
            "access_slots",
        },
        "explicit_permissions": {
            "articles.assign_articlecategory",
            "articles.edit_article",
            "articles.trigger_article_placement",
            "placements.manage_manual_categoryplacement",
            "placements.view_system_categoryplacement",
        },
        "model_access": {
            "articles": {"view", "add", "change"},
            "placements.ArticlePlacement": {"view", "add", "change"},
            "placements.LayoutSlot": {"view"},
            "journals.PublicationIssue": {"view", "add", "change"},
            "journals.IssueArticle": {"view", "add", "change"},
            "news": {"view", "add", "change"},
            "home": {"view", "add", "change"},
            "standardpages": {"view", "add", "change"},
        },
        "page_permissions": {"add_page", "change_page", "lock_page", "unlock_page"},
    },
    "reviewer": {
        "display_name": "审核人员",
        "description": "负责文章审核、驳回和审核意见，可只读查看系统投放。",
        "custom_permissions": {"access_article_review", "review_articles"},
        "explicit_permissions": {"articles.review_article"},
        "model_access": {
            "articles": {"view", "change"},
            "news": {"view", "change"},
        },
        "page_permissions": {"change_page"},
    },
    "site_operator": {
        "display_name": "站点运营",
        "description": "维护子期刊资料、封面图、统计图表和 SEO，默认不维护动态栏目。",
        "custom_permissions": {"access_journals", "access_site_settings"},
        "explicit_permissions": {
            "site_settings.view_main_navigation",
            "site_settings.view_journal_navigation",
        },
        "model_access": {
            "journals.Journal": {"view", "add", "change"},
            "journals.PublicationIssue": {"view", "add", "change"},
            "journals.IssueArticle": {"view", "add", "change"},
            "images": {"view", "add", "change"},
            "site_settings.SiteSettings": {"view", "change"},
            "site_settings.NavigationSet": {"view"},
            "site_settings.NavigationGroup": {"view"},
            "site_settings.NavigationItem": {"view"},
            "site_settings.ContentColumnConfig": {"view"},
        },
    },
    "publisher": {
        "display_name": "发布管理员",
        "description": "负责静态生成、发布、同步失败重试和回滚，不编辑文章。",
        "custom_permissions": {"access_static_publish", "access_audit_log"},
        "explicit_permissions": {
            "static_publish.publish_static_site",
            "static_publish.publish_category_pages",
            "static_publish.retry_category_publish",
            "static_publish.rollback_category_publish",
            "journals.publish_publication_issue",
            "journals.set_current_publication_issue",
            "journals.rollback_publication_issue",
        },
        "model_access": {
            "static_publish": {"view", "add", "change"},
            "site_settings.AuditLog": {"view"},
            "journals.PublicationIssue": {"view"},
            "journals.IssueArticle": {"view"},
        },
    },
    "readonly": {
        "display_name": "只读人员",
        "description": "只读查看配置、发布记录、系统投放和审计日志。",
        "custom_permissions": {
            "access_site_settings",
            "access_static_publish",
            "access_audit_log",
        },
        "explicit_permissions": {
            "site_settings.view_main_navigation",
            "site_settings.view_journal_navigation",
            "site_settings.view_navigation_template",
        },
        "model_access": {
            "static_publish": {"view"},
            "site_settings.SiteSettings": {"view"},
            "site_settings.NavigationSet": {"view"},
            "site_settings.NavigationGroup": {"view"},
            "site_settings.NavigationItem": {"view"},
            "site_settings.ContentColumnConfig": {"view"},
            "journals.PublicationIssue": {"view"},
            "journals.IssueArticle": {"view"},
            "site_settings.AdminRolePreset": {"view"},
            "site_settings.AuditLog": {"view"},
        },
    },
}


class Command(BaseCommand):
    help = "Create or update the standard AI Author Forum Wagtail role groups."

    def handle(self, *args, **options):
        content_type = ContentType.objects.get_for_model(AdminRolePreset)
        all_permissions = Permission.objects.all()
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
        """Grant collection-scoped media permissions required by Wagtail.

        Wagtail's image/document permission policies do not use the ordinary
        ``group.permissions`` table for collection-backed media. Without these
        records a role can see the custom menu (because it has
        ``wagtailimages.view_image``) but is redirected from the index with
        "no permission".
        """

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
