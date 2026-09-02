from django.urls import include, path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.utils.admin_i18n import admin_text


class StaticPublishMenuItem(MenuItem):
    def is_shown(self, request):
        return is_super_admin(request.user)


@hooks.register("register_admin_urls")
def register_admin_urls():
    return [
        path(
            "static-publish/",
            include(("ai_author_forum.static_publish.urls", "static_publish")),
        )
    ]


@hooks.register("register_admin_menu_item")
def register_admin_menu_item():
    return StaticPublishMenuItem(
        admin_text("static_publish"),
        reverse("static_publish:center"),
        icon_name="upload",
        order=900,
    )
