from __future__ import annotations

from collections.abc import Callable

from django.urls import reverse_lazy
from wagtail.admin.menu import Menu, MenuItem, SubmenuMenuItem

from ai_author_forum.utils.admin_i18n import admin_text

from .permissions import get_admin_permission_context


class BusinessMenuItem(MenuItem):
    """Menu item controlled by the project's canonical permission flags."""

    def __init__(
        self,
        *args,
        permission_flags: tuple[str, ...] = (),
        permissions: tuple[str, ...] = (),
        predicate: Callable | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.permission_flags = permission_flags
        self.permissions = permissions
        self.predicate = predicate

    def is_shown(self, request):
        user = request.user
        if user.is_superuser:
            return True
        if self.predicate is not None:
            return bool(self.predicate(request))
        flags = get_admin_permission_context(user)
        return any(flags.get(flag, False) for flag in self.permission_flags) or any(
            user.has_perm(permission) for permission in self.permissions
        )


class NavigationBusinessMenuItem(BusinessMenuItem):
    """Open the first navigation scope that the current user can actually view."""

    scope_permissions = (
        (
            "main",
            (
                "site_settings.view_main_navigation",
                "site_settings.manage_main_navigation",
            ),
        ),
        (
            "journal",
            (
                "site_settings.view_journal_navigation",
                "site_settings.manage_journal_navigation",
            ),
        ),
        (
            "template",
            (
                "site_settings.view_navigation_template",
                "site_settings.manage_navigation_template",
            ),
        ),
    )

    def render_component(self, request):
        url = str(self.url)
        if not request.user.is_superuser:
            for mode, permissions in self.scope_permissions:
                if any(request.user.has_perm(permission) for permission in permissions):
                    if mode != "main":
                        url = f"{url}?mode={mode}"
                    break
        return MenuItem(
            self.label,
            url,
            name=self.name,
            classname=self.classname,
            icon_name=self.icon_name,
            attrs=self.attrs.copy(),
            order=self.order,
        ).render_component(request)


class SiteSettingsBusinessMenuItem(BusinessMenuItem):
    """Send readers to a summary and editors to Wagtail's settings form."""

    def __init__(self, *args, edit_url, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_url = edit_url

    def render_component(self, request):
        url = str(self.url)
        if request.user.is_superuser or request.user.has_perm(
            "site_settings.change_sitesettings"
        ):
            url = str(self.edit_url)
        return MenuItem(
            self.label,
            url,
            name=self.name,
            classname=self.classname,
            icon_name=self.icon_name,
            attrs=self.attrs.copy(),
            order=self.order,
        ).render_component(request)


_FLAT_MENU_NAMES = {
    "all-articles",
    "pending-articles",
    "placement-sync-exceptions",
    "article-review",
    "journals",
    "placements",
    "layout-slots",
    "system-category-placements",
    "static-publish",
    "managed-navigation",
    "images",
    "documents",
    "explorer",
    "snippets",
}
_FLAT_MENU_URLS = {
    "/admin/articles/",
    "/admin/articles/pending/",
    "/admin/article-review/",
    "/admin/journals/",
    "/admin/placements/",
    "/admin/layout-slots/",
    "/admin/system-category-placements/",
    "/admin/system-category-placements/?errors=1",
    "/admin/managed-navigation/",
    "/admin/journals/categories/",
    "/admin/journals/categories/audit/",
    "/admin/journals/import/",
    "/admin/static-publish/",
    "/admin/images/",
    "/admin/documents/",
    "/admin/pages/",
    "/admin/snippets/",
}
_SETTINGS_MENU_NAMES = {
    "navigation-baseline",
    "audit-logs",
}
_SETTINGS_MENU_URLS = {
    "/admin/settings/site_settings/sitesettings/",
}


def _take_existing(menu_items, *, name=None, url=None):
    for item in list(menu_items):
        if (name and item.name == name) or (url and str(item.url) == url):
            menu_items.remove(item)
            return item
    return None


def _business_item(
    label,
    url_name,
    *,
    name,
    icon_name,
    order,
    flags=(),
    permissions=(),
    predicate=None,
    query="",
):
    url = reverse_lazy(url_name)
    if query:
        url = f"{url}?{query}"
    return BusinessMenuItem(
        label,
        url,
        name=name,
        icon_name=icon_name,
        order=order,
        permission_flags=tuple(flags),
        permissions=tuple(permissions),
        predicate=predicate,
    )


def _content_menu():
    items = [
        _business_item(
            admin_text("articles.manage"),
            "article_admin:index",
            name="business-articles",
            icon_name="doc-full",
            order=10,
            flags=("can_view_articles", "can_edit_article"),
            permissions=("site_settings.access_articles",),
        ),
        _business_item(
            admin_text("articles.pending_review"),
            "article-review:index",
            name="business-review",
            icon_name="list-ul",
            order=20,
            flags=("can_view_article_review", "can_review_article"),
            permissions=("site_settings.access_article_review",),
        ),
        _business_item(
            admin_text("placements.sync_errors"),
            "system-category-placements:index",
            name="business-placement-errors",
            icon_name="warning",
            order=30,
            permissions=("placements.view_system_categoryplacement",),
            query="errors=1",
        ),
    ]
    items[0].label = admin_text("articles.manage")
    items[0].order = 10
    items[1].order = 20
    items[2].order = 30
    return Menu(items=items)


def _journal_menu():
    items = [
        _business_item(
            admin_text("journals.list"),
            "journals:index",
            name="business-journals",
            icon_name="doc-full",
            order=10,
            flags=("can_view_journals", "can_change_journal", "can_add_journal"),
            permissions=("site_settings.access_journals",),
        ),
        _business_item(
            admin_text("journals.categories"),
            "journals_category_admin",
            name="business-journal-categories",
            icon_name="folder-open-inverse",
            order=20,
            permissions=(
                "journals.view_journalcategory",
                "journals.add_journalcategory",
                "journals.change_journalcategory",
            ),
        ),
        _business_item(
            admin_text("journals.import"),
            "journals_import_dashboard",
            name="business-journal-import",
            icon_name="upload",
            order=30,
            flags=("can_import_journals",),
        ),
        _business_item(
            admin_text("site_settings.navigation_audit_logs"),
            "journals_category_audit",
            name="business-journal-audit",
            icon_name="history",
            order=40,
            predicate=lambda request: (
                request.user.has_perm("site_settings.access_audit_log")
                and request.user.has_perm("journals.view_journalcategory")
            ),
        ),
    ]
    for order, item in enumerate(items, 1):
        item.order = order * 10
    return Menu(items=items)


def _delivery_menu():
    items = [
        _business_item(
            admin_text("placements.homepage"),
            "homepage-composition:index",
            name="business-homepage-composition",
            icon_name="home",
            order=10,
            flags=("can_view_placements", "can_manage_placement"),
            permissions=("site_settings.access_placements",),
        ),
        _business_item(
            admin_text("placements.all"),
            "placements:index",
            name="business-placements",
            icon_name="pick",
            order=20,
            flags=("can_view_placements", "can_manage_placement"),
            permissions=("site_settings.access_placements",),
        ),
        _business_item(
            admin_text("placements.slots"),
            "layout-slots:index",
            name="business-layout-slots",
            icon_name="list-ul",
            order=30,
            flags=("can_view_slots", "can_manage_placement"),
            permissions=("site_settings.access_slots",),
        ),
        _business_item(
            admin_text("placements.system_categories"),
            "system-category-placements:index",
            name="business-system-placements",
            icon_name="view",
            order=30,
            permissions=("placements.view_system_categoryplacement",),
        ),
        _business_item(
            admin_text("static_publish"),
            "static_publish:center",
            name="business-static-publish",
            icon_name="upload",
            order=40,
            flags=(
                "can_view_static_publish",
                "can_publish_static",
                "can_retry_publish",
                "can_rollback_publish",
            ),
            permissions=("site_settings.access_static_publish",),
        ),
    ]
    for order, item in enumerate(items, 1):
        item.order = order * 10
    return Menu(items=items)


def _assets_menu():
    images = BusinessMenuItem(
        admin_text("site_settings.images"),
        reverse_lazy("wagtailimages:index"),
        name="business-images",
        icon_name="image",
        order=10,
        permissions=(
            "wagtailimages.view_image",
            "wagtailimages.add_image",
            "wagtailimages.change_image",
        ),
    )
    documents = BusinessMenuItem(
        admin_text("site_settings.documents"),
        reverse_lazy("wagtaildocs:index"),
        name="business-documents",
        icon_name="doc-full-inverse",
        order=20,
        permissions=(
            "wagtaildocs.view_document",
            "wagtaildocs.add_document",
            "wagtaildocs.change_document",
        ),
    )
    images.order = 10
    documents.order = 20
    return Menu(
        items=[
            images,
            documents,
            NavigationBusinessMenuItem(
                admin_text("site_settings.navigation"),
                reverse_lazy("managed_navigation_admin"),
                name="business-navigation",
                icon_name="site",
                order=30,
                permissions=(
                    "site_settings.view_main_navigation",
                    "site_settings.manage_main_navigation",
                    "site_settings.view_journal_navigation",
                    "site_settings.manage_journal_navigation",
                    "site_settings.view_navigation_template",
                    "site_settings.manage_navigation_template",
                ),
            ),
            SiteSettingsBusinessMenuItem(
                admin_text("site_settings"),
                reverse_lazy("site_settings_summary"),
                edit_url=reverse_lazy(
                    "wagtailsettings:edit",
                    kwargs={"app_name": "site_settings", "model_name": "sitesettings"},
                ),
                name="business-site-settings",
                icon_name="cog",
                order=40,
                permissions=(
                    "site_settings.access_site_settings",
                    "site_settings.view_sitesettings",
                    "site_settings.change_sitesettings",
                ),
            ),
            _business_item(
                admin_text("site_settings.content_readiness"),
                "content_readiness_admin",
                name="business-content-readiness",
                icon_name="success",
                order=50,
                permissions=(
                    "site_settings.access_site_settings",
                    "site_settings.view_sitesettings",
                    "site_settings.change_sitesettings",
                    "static_publish.view_staticpublishjob",
                ),
            ),
            _business_item(
                admin_text("site_settings.audit_logs"),
                "auditlog:index",
                name="business-audit-log",
                icon_name="history",
                order=60,
                flags=("can_view_audit_log",),
            ),
        ]
    )


def construct_business_navigation(request, menu_items):
    """Replace the flat technical menu with visible business-domain submenus."""

    for item in list(menu_items):
        url = str(item.url)
        if item.name in _FLAT_MENU_NAMES or url in _FLAT_MENU_URLS:
            menu_items.remove(item)

    domains = (
        (
            admin_text("site_settings.content_domain"),
            _content_menu(),
            "business-content",
            "edit",
            200,
        ),
        (
            admin_text("site_settings.journal_domain"),
            _journal_menu(),
            "business-journals-domain",
            "folder-open-inverse",
            210,
        ),
        (
            admin_text("site_settings.delivery"),
            _delivery_menu(),
            "business-delivery",
            "pick",
            220,
        ),
        (
            admin_text("site_settings.assets"),
            _assets_menu(),
            "business-assets",
            "image",
            230,
        ),
    )
    for label, submenu, name, icon_name, order in domains:
        if not submenu.menu_items_for_request(request):
            continue
        menu_items.append(
            SubmenuMenuItem(
                label,
                submenu,
                name=name,
                icon_name=icon_name,
                order=order,
            )
        )


def construct_business_settings_menu(request, menu_items):
    """Avoid duplicating business-facing configuration inside Wagtail settings."""

    if not get_admin_permission_context(request.user)["has_dashboard_access"]:
        return
    menu_items[:] = [
        item
        for item in menu_items
        if item.name not in _SETTINGS_MENU_NAMES
        and str(item.url) not in _SETTINGS_MENU_URLS
    ]
