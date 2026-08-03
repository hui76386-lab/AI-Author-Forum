from __future__ import annotations

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import path
from django.utils.functional import cached_property
from wagtail.admin.menu import MenuItem
from wagtail.admin.viewsets.base import ViewSet
from wagtail.models import Site
from wagtail.permissions import ModelPermissionPolicy

from ai_author_forum.static_publish.readiness import check_content_readiness
from ai_author_forum.utils.admin_i18n import admin_text

from .models import SiteSettings

SITE_SETTINGS_VIEW_PERMISSIONS = (
    "site_settings.access_site_settings",
    "site_settings.view_sitesettings",
    "site_settings.change_sitesettings",
)


def can_view_site_settings(user) -> bool:
    return user.is_superuser or any(
        user.has_perm(permission) for permission in SITE_SETTINGS_VIEW_PERMISSIONS
    )


@permission_required("wagtailadmin.access_admin", raise_exception=True)
def content_readiness_admin(request):
    if not can_view_site_settings(request.user) and not request.user.has_perm(
        "static_publish.view_staticpublishjob"
    ):
        raise PermissionDenied
    result = check_content_readiness()
    return render(
        request,
        "wagtailadmin/site_settings/content_readiness.html",
        {
            "title": admin_text("site_settings.content_readiness"),
            "result": result,
        },
    )


@permission_required("wagtailadmin.access_admin", raise_exception=True)
def site_settings_summary(request):
    """Render SiteSettings as a genuinely read-only business summary."""

    if not can_view_site_settings(request.user):
        raise PermissionDenied

    site = Site.find_for_request(request)
    if site is None:
        site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
    if site is None:
        raise PermissionDenied

    site_settings = SiteSettings.for_site(site)
    can_change = request.user.is_superuser or request.user.has_perm(
        "site_settings.change_sitesettings"
    )
    return render(
        request,
        "wagtailadmin/site_settings/summary.html",
        {
            "title": "\u4e3b\u7ad9\u914d\u7f6e",
            "site": site,
            "site_settings": site_settings,
            "can_change": can_change,
        },
    )


class PermissionedModuleViewSet(ViewSet):
    add_to_admin_menu = True
    permission = ""
    title = ""
    description = ""
    owner = ""
    integration_points: tuple[str, ...] = ()

    @cached_property
    def menu_item_class(self):
        permission = self.permission

        class PermissionedMenuItem(MenuItem):
            def is_shown(self, request):
                return request.user.is_superuser or request.user.has_perm(permission)

        return PermissionedMenuItem

    def has_access(self, request) -> bool:
        return request.user.is_superuser or request.user.has_perm(self.permission)

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied
        return render(
            request,
            "admin/module_index.html",
            {
                "title": self.title,
                "description": self.description,
                "owner": self.owner,
                "integration_points": self.integration_points,
            },
        )

    def get_urlpatterns(self):
        return [path("", self.index_view, name="index")]


class CoreNavigationPermissionPolicy(ModelPermissionPolicy):
    def user_has_permission(self, user, action):
        if action in {"add", "change", "delete"}:
            return user.is_superuser or user.has_perm(
                "site_settings.manage_core_navigation"
            )
        return super().user_has_permission(user, action)

    def user_has_any_permission(self, user, actions):
        return any(self.user_has_permission(user, action) for action in actions)


class ScopeNavigationPermissionPolicy(ModelPermissionPolicy):
    def __init__(self, model, *, view_permission, manage_permission):
        super().__init__(model)
        self.view_permission = view_permission
        self.manage_permission = manage_permission

    def user_has_permission(self, user, action):
        if user.is_superuser:
            return True
        if action in {"add", "change", "delete"}:
            return user.has_perm(self.manage_permission)
        return user.has_perm(self.view_permission) or user.has_perm(
            self.manage_permission
        )

    def user_has_any_permission(self, user, actions):
        return any(self.user_has_permission(user, action) for action in actions)


class ReadOnlyModelPermissionPolicy(ModelPermissionPolicy):
    def user_has_permission(self, user, action):
        if action in {"add", "change", "delete"}:
            return False
        return super().user_has_permission(user, action)

    def user_has_any_permission(self, user, actions):
        readable_actions = [
            action for action in actions if action not in {"add", "change", "delete"}
        ]
        if not readable_actions:
            return False
        return super().user_has_any_permission(user, readable_actions)
