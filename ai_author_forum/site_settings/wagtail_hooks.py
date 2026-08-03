from django.shortcuts import redirect
from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.views.generic.models import IndexView
from wagtail.admin.viewsets import ViewSetGroup
from wagtail.admin.viewsets.model import ModelViewSet

from ai_author_forum.utils.admin_i18n import admin_text

from .admin_navigation import (
    construct_business_navigation,
    construct_business_settings_menu,
)
from .admin_views import (
    CoreNavigationPermissionPolicy,
    ReadOnlyModelPermissionPolicy,
    ScopeNavigationPermissionPolicy,
    content_readiness_admin,
    site_settings_summary,
)
from .dashboard import RoleDashboardPanel, should_show_role_dashboard
from .models import (
    AdminRolePreset,
    AuditLog,
    ContentColumnConfig,
    NavigationGroup,
    NavigationItem,
    NavigationScope,
    NavigationSet,
)
from .navigation_admin import managed_navigation_admin


class MainNavigationIndexView(IndexView):
    def get(self, request, *args, **kwargs):
        return redirect(f"{reverse('managed_navigation_admin')}?mode=main")

    def get_base_queryset(self):
        return (
            super()
            .get_base_queryset()
            .filter(
                scope=NavigationScope.MAIN_SITE,
                journal__isnull=True,
                is_template=False,
            )
        )


class JournalNavigationIndexView(IndexView):
    def get(self, request, *args, **kwargs):
        return redirect(f"{reverse('managed_navigation_admin')}?mode=journal")

    def get_base_queryset(self):
        return (
            super()
            .get_base_queryset()
            .filter(
                scope=NavigationScope.JOURNAL,
                journal__isnull=False,
                is_template=False,
            )
        )


class TemplateNavigationIndexView(IndexView):
    def get(self, request, *args, **kwargs):
        return redirect(f"{reverse('managed_navigation_admin')}?mode=template")

    def get_base_queryset(self):
        return super().get_base_queryset().filter(is_template=True)


class NavigationSetViewSet(ModelViewSet):
    model = NavigationSet
    view_permission = "site_settings.view_main_navigation"
    manage_permission = "site_settings.manage_main_navigation"

    @property
    def permission_policy(self):
        return ScopeNavigationPermissionPolicy(
            self.model,
            view_permission=self.view_permission,
            manage_permission=self.manage_permission,
        )

    menu_icon = "site"
    inspect_view_enabled = True
    list_display = ["name", "scope", "journal", "status", "version", "updated_at"]
    list_filter = ["status", "site"]
    search_fields = ["name", "journal__name", "journal__slug"]
    form_fields = [
        "scope",
        "site",
        "journal",
        "name",
        "status",
        "is_template",
        "copied_from_template",
    ]


class MainNavigationSetViewSet(NavigationSetViewSet):
    menu_label = admin_text("site_settings.main_navigation")
    menu_name = "main-navigation"
    name = "main_navigation"
    index_view_class = MainNavigationIndexView


class JournalNavigationSetViewSet(NavigationSetViewSet):
    menu_label = admin_text("site_settings.journal_navigation")
    menu_name = "journal-navigation"
    name = "journal_navigation"
    index_view_class = JournalNavigationIndexView


class TemplateNavigationSetViewSet(NavigationSetViewSet):
    menu_label = admin_text("site_settings.journal_template")
    menu_name = "journal-navigation-template"
    name = "journal_navigation_template"
    index_view_class = TemplateNavigationIndexView


class NavigationGroupViewSet(ModelViewSet):
    model = NavigationGroup
    menu_label = admin_text("site_settings.navigation_groups")
    menu_name = "managed-navigation-groups"
    menu_icon = "folder-open-inverse"
    inspect_view_enabled = True
    list_display = [
        "label",
        "code",
        "navigation_set",
        "sort_order",
        "is_visible",
        "status",
    ]
    list_filter = ["navigation_set", "is_visible", "status"]
    search_fields = ["label", "code", "navigation_set__name"]
    form_fields = [
        "navigation_set",
        "label",
        "code",
        "sort_order",
        "is_visible",
        "status",
    ]


class NavigationItemViewSet(ModelViewSet):
    model = NavigationItem
    menu_label = admin_text("site_settings.navigation_baseline")
    menu_name = "navigation-baseline"
    menu_icon = "site"
    menu_order = 100
    add_to_settings_menu = True
    inspect_view_enabled = True
    list_display = [
        "label",
        "code",
        "group",
        "target_type",
        "sort_order",
        "is_visible",
        "status",
    ]
    list_filter = ["group", "target_type", "is_visible", "status", "site"]
    search_fields = ["label", "slug", "code"]

    @property
    def permission_policy(self):
        return CoreNavigationPermissionPolicy(self.model)

    form_fields = [
        "site",
        "area",
        "label",
        "slug",
        "parent",
        "group",
        "code",
        "target_type",
        "category",
        "page",
        "internal_path",
        "external_url",
        "url",
        "open_in_new_tab",
        "sort_order",
        "is_active",
        "is_visible",
        "status",
        "allow_direct_access",
        "is_core",
    ]


class ContentColumnConfigViewSet(ModelViewSet):
    model = ContentColumnConfig
    menu_label = admin_text("site_settings.content_columns")
    menu_name = "content-column-configs"
    menu_icon = "doc-full-inverse"
    inspect_view_enabled = True
    list_display = [
        "navigation_item",
        "category",
        "template_variant",
        "minimum_publish_items",
        "empty_behavior",
        "enable_type_filter",
        "enable_year_filter",
        "page_size",
    ]
    list_filter = [
        "template_variant",
        "empty_behavior",
        "enable_type_filter",
        "enable_year_filter",
    ]
    search_fields = [
        "navigation_item__label",
        "navigation_item__code",
        "category__name",
    ]
    form_fields = [
        "navigation_item",
        "intro",
        "cover_image",
        "category",
        "template_variant",
        "default_sort",
        "minimum_publish_items",
        "empty_behavior",
        "show_open_access_badge",
        "show_authors",
        "show_abstract",
        "enable_type_filter",
        "enable_year_filter",
        "page_size",
        "seo_title",
        "seo_description",
        "empty_message",
    ]


class AuditLogViewSet(ModelViewSet):
    model = AuditLog
    menu_label = admin_text("site_settings.audit_logs")
    menu_name = "audit-logs"
    menu_icon = "date"
    menu_order = 300
    add_to_settings_menu = True
    inspect_view_enabled = True
    copy_view_enabled = False
    list_display = ["created_at", "action", "status", "actor", "target_label"]
    list_filter = ["action", "status", "created_at"]
    search_fields = ["target_label", "target_type", "target_id", "message"]
    form_fields = []
    inspect_view_fields = [
        "created_at",
        "action",
        "status",
        "actor",
        "target_type",
        "target_id",
        "target_label",
        "message",
        "metadata",
        "request_id",
        "ip_address",
    ]

    @property
    def permission_policy(self):
        return ReadOnlyModelPermissionPolicy(self.model)


class NavigationAuditLogViewSet(AuditLogViewSet):
    name = "navigation_audit_logs"
    menu_label = admin_text("site_settings.navigation_audit_logs")
    menu_name = "navigation-audit-logs"
    menu_icon = "history"
    add_to_settings_menu = False


class ManagedNavigationViewSetGroup(ViewSetGroup):
    menu_label = admin_text("site_settings.navigation")
    menu_name = "managed-navigation"
    menu_icon = "site"
    menu_order = 150
    items = (
        MainNavigationSetViewSet,
        JournalNavigationSetViewSet,
        TemplateNavigationSetViewSet,
        NavigationAuditLogViewSet,
    )


class AdminRolePresetViewSet(ModelViewSet):
    model = AdminRolePreset
    menu_label = admin_text("site_settings.role_presets")
    menu_name = "admin-role-presets"
    menu_icon = "group"
    menu_order = 200
    add_to_settings_menu = True
    inspect_view_enabled = True
    list_display = ["display_name", "role_code", "group", "is_active", "is_system"]
    list_filter = ["is_active", "is_system"]
    search_fields = ["display_name", "role_code"]
    form_fields = [
        "role_code",
        "display_name",
        "description",
        "group",
        "is_active",
        "is_system",
    ]


@hooks.register("register_admin_viewset")
def register_site_settings_viewsets():
    return [
        ManagedNavigationViewSetGroup(),
        NavigationItemViewSet(),
        AdminRolePresetViewSet(),
        AuditLogViewSet(),
    ]


@hooks.register("register_admin_urls")
def register_managed_navigation_urls():
    return [
        path(
            "managed-navigation/",
            managed_navigation_admin,
            name="managed_navigation_admin",
        ),
        path(
            "site-settings-summary/",
            site_settings_summary,
            name="site_settings_summary",
        ),
        path(
            "content-readiness/",
            content_readiness_admin,
            name="content_readiness_admin",
        ),
    ]


@hooks.register("construct_main_menu")
def group_business_admin_menu(request, menu_items):
    construct_business_navigation(request, menu_items)


@hooks.register("construct_settings_menu")
def simplify_business_settings_menu(request, menu_items):
    construct_business_settings_menu(request, menu_items)


@hooks.register("construct_homepage_panels")
def add_role_dashboard_panel(request, panels):
    if should_show_role_dashboard(request.user):
        panels[:] = [RoleDashboardPanel()]
