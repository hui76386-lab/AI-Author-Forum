from ai_author_forum.site_settings.admin_views import PermissionedModuleViewSet
from ai_author_forum.utils.admin_i18n import admin_text


class StaticPublishViewSet(PermissionedModuleViewSet):
    name = "static-publish"
    menu_label = admin_text("static_publish")
    menu_name = "static-publish"
    menu_icon = "download"
    menu_order = 250
    permission = "site_settings.access_static_publish"
    title = admin_text("static_publish")
    description = admin_text("static_publish.description")
    owner = "E：static_publish 应用；A 提供发布权限和审计日志回写能力。"
    integration_points = (
        "build_static_site",
        "manifest",
        "AuditLog.record(action=publish)",
    )
