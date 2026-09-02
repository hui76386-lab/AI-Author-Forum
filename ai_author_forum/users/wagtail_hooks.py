from django.urls import include, path, reverse_lazy
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from ai_author_forum.site_settings.access_control import can_manage_accounts
from ai_author_forum.utils.admin_i18n import admin_text


class AccountMenuItem(MenuItem):
    def is_shown(self, request):
        return can_manage_accounts(request.user)


@hooks.register("register_admin_urls")
def register_account_admin_urls():
    return [path("accounts/", include("ai_author_forum.users.urls"))]


@hooks.register("register_admin_menu_item")
def register_account_menu_item():
    return AccountMenuItem(
        admin_text("accounts.manage"),
        reverse_lazy("account_admin:index"),
        name="account-management",
        icon_name="user",
        order=180,
    )
