import importlib
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from wagtail.admin.menu import admin_menu, settings_menu
from wagtail.models import GroupCollectionPermission, GroupPagePermission

from ai_author_forum.site_settings.admin_views import CoreNavigationPermissionPolicy
from ai_author_forum.site_settings.management.commands.seed_roles import (
    ROLE_DEFINITIONS,
)
from ai_author_forum.site_settings.models import (
    AuditAction,
    AuditLog,
    AuditStatus,
    NavigationItem,
)

importlib.import_module("ai_author_forum.urls")


class FoundationPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_navigation", verbosity=0)
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.factory = RequestFactory()

    def make_staff_user(self, role_name):
        user = self.user_model.objects.create_user(
            username=f"{role_name}-user-{self.user_model.objects.count()}",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(*user.groups.model.objects.filter(name=role_name))
        return user

    def visible_module_labels(self, user):
        request = self.factory.get("/admin/")
        request.user = user
        labels = {
            "子期刊",
            "文章管理",
            "文章审核",
            "投放管理",
            "版位编排",
            "静态发布",
        }
        return {
            item.label
            for item in admin_menu.registered_menu_items
            if item.label in labels and item.is_shown(request)
        }

    def visible_settings_labels(self, user):
        request = self.factory.get("/admin/settings/")
        request.user = user
        labels = {"导航基线", "角色权限预设", "审计日志"}
        return {
            item.label
            for item in settings_menu.registered_menu_items
            if item.label in labels and item.is_shown(request)
        }

    def test_publisher_role_uses_canonical_static_publish_permission(self):
        group = Group.objects.get(name=ROLE_DEFINITIONS["publisher"]["display_name"])
        permissions = set(
            group.permissions.values_list(
                "content_type__app_label",
                "codename",
            )
        )

        self.assertIn(
            ("static_publish", "publish_static_site"),
            permissions,
        )
        self.assertNotIn(
            ("site_settings", "publish_static_site"),
            permissions,
        )
        self.assertNotIn(
            ("site_settings", "rollback_static_site"),
            permissions,
        )

        publisher = self.make_staff_user(group.name)
        self.assertTrue(publisher.has_perm("static_publish.publish_static_site"))
        self.assertFalse(publisher.has_perm("site_settings.publish_static_site"))
        self.assertFalse(publisher.has_perm("site_settings.rollback_static_site"))

    def test_legacy_publish_permissions_are_migrated_to_canonical_permission(self):
        migration = importlib.import_module(
            "ai_author_forum.site_settings.migrations."
            "0003_alter_adminrolepreset_options"
        )
        content_type = ContentType.objects.get(
            app_label="site_settings",
            model="adminrolepreset",
        )
        legacy_publish = Permission.objects.create(
            content_type=content_type,
            codename="publish_static_site",
            name="Can publish static site",
        )
        legacy_rollback = Permission.objects.create(
            content_type=content_type,
            codename="rollback_static_site",
            name="Can rollback static site",
        )
        legacy_group = Group.objects.create(name="legacy-publisher")
        legacy_group.permissions.add(legacy_publish)
        direct_user = self.user_model.objects.create_user(
            username="legacy-direct-publisher",
            password="test-password",
            is_staff=True,
        )
        direct_user.user_permissions.add(legacy_rollback)

        migration.migrate_legacy_publish_permissions(
            apps,
            SimpleNamespace(connection=connection),
        )

        canonical = Permission.objects.get(
            content_type__app_label="static_publish",
            codename="publish_static_site",
        )
        self.assertTrue(legacy_group.permissions.filter(pk=canonical.pk).exists())
        self.assertTrue(direct_user.user_permissions.filter(pk=canonical.pk).exists())
        self.assertFalse(
            Permission.objects.filter(
                content_type=content_type,
                codename__in=migration.LEGACY_PERMISSIONS,
            ).exists()
        )

    def test_roles_expose_different_module_menus(self):
        self.assertEqual(
            self.visible_module_labels(self.make_staff_user("内容管理员")),
            {"文章管理", "投放管理"},
        )
        self.assertEqual(
            self.visible_module_labels(self.make_staff_user("审核人员")),
            {"文章审核"},
        )
        self.assertEqual(
            self.visible_module_labels(self.make_staff_user("站点运营")),
            {"子期刊"},
        )
        self.assertEqual(
            self.visible_module_labels(self.make_staff_user("发布管理员")),
            {"静态发布"},
        )
        self.assertEqual(
            self.visible_module_labels(self.make_staff_user("只读人员")),
            {"静态发布"},
        )

    def test_rendered_admin_sidebar_contains_only_role_menu_urls(self):
        cases = {
            "内容管理员": (
                {"/admin/articles/", "/admin/placements/"},
                {"/admin/article-review/", "/admin/journals/"},
            ),
            "审核人员": (
                {"/admin/article-review/"},
                {"/admin/articles/", "/admin/placements/"},
            ),
            "站点运营": (
                {"/admin/journals/"},
                {"/admin/articles/", "/admin/placements/", "/admin/layout-slots/"},
            ),
            "发布管理员": ({"/admin/static-publish/"}, {"/admin/journals/"}),
            "只读人员": ({"/admin/static-publish/"}, {"/admin/article-review/"}),
        }
        for role_name, (visible_urls, hidden_urls) in cases.items():
            client = Client()
            client.force_login(self.make_staff_user(role_name))
            content = client.get("/admin/").content.decode("utf-8")
            for url in visible_urls:
                self.assertIn(url, content)
            for url in hidden_urls:
                self.assertNotIn(f'href="{url}"', content)

    def test_site_settings_are_not_available_to_content_editors(self):
        content_client = Client()
        content_client.force_login(self.make_staff_user("内容管理员"))
        operator_client = Client()
        operator_client.force_login(self.make_staff_user("站点运营"))

        denied = content_client.get("/admin/settings/site_settings/sitesettings/")
        allowed = operator_client.get("/admin/settings/site_settings/sitesettings/")
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers["Location"], "/admin/")
        self.assertEqual(allowed.status_code, 302)
        self.assertIn(
            "/admin/settings/site_settings/sitesettings/", allowed.headers["Location"]
        )

    def test_all_standard_roles_can_log_in_to_wagtail_admin(self):
        for role_name in (
            "超级管理员",
            "内容管理员",
            "审核人员",
            "站点运营",
            "发布管理员",
            "只读人员",
        ):
            with self.subTest(role=role_name):
                client = Client()
                client.force_login(self.make_staff_user(role_name))
                self.assertEqual(client.get("/admin/").status_code, 200)

    def test_journals_module_renders_real_list_page(self):
        client = Client()
        client.force_login(self.make_staff_user("站点运营"))

        response = client.get("/admin/journals/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "子期刊列表")
        self.assertContains(response, "新增子期刊")
        self.assertNotContains(response, "批量导入")
        self.assertNotContains(response, "此页面用于统一后台入口和权限边界")

    def test_article_review_module_renders_dashboard(self):
        client = Client()
        client.force_login(self.make_staff_user("审核人员"))

        response = client.get("/admin/article-review/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "待审核文章")
        self.assertContains(response, "最近处理")
        self.assertContains(response, "打开待审核列表")
        self.assertNotContains(response, "此页面用于统一后台入口和权限边界")

    def test_module_urls_enforce_role_permissions(self):
        allowed_paths = {
            "content_manager": {
                "/admin/articles/",
                "/admin/placements/",
                "/admin/layout-slots/",
                "/admin/system-category-placements/",
            },
            "reviewer": {"/admin/article-review/"},
            "site_operator": {
                "/admin/journals/",
                "/admin/managed-navigation/",
                "/admin/navigationitem/",
            },
            "publisher": {"/admin/static-publish/", "/admin/auditlog/"},
            "readonly": {
                "/admin/static-publish/",
                "/admin/auditlog/",
                "/admin/managed-navigation/",
                "/admin/navigationitem/",
                "/admin/adminrolepreset/",
            },
        }
        denied_paths = {
            "content_manager": {
                "/admin/article-review/",
                "/admin/journals/",
                "/admin/journals/import/",
                "/admin/journals/categories/",
                "/admin/static-publish/",
                "/admin/managed-navigation/",
            },
            "reviewer": {
                "/admin/articles/",
                "/admin/journals/",
                "/admin/placements/",
                "/admin/layout-slots/",
                "/admin/system-category-placements/",
                "/admin/static-publish/",
                "/admin/navigationitem/",
            },
            "site_operator": {
                "/admin/articles/",
                "/admin/article-review/",
                "/admin/journals/import/",
                "/admin/journals/categories/",
                "/admin/placements/",
                "/admin/layout-slots/",
                "/admin/static-publish/",
                "/admin/auditlog/",
                "/admin/adminrolepreset/",
            },
            "publisher": {
                "/admin/articles/",
                "/admin/article-review/",
                "/admin/journals/",
                "/admin/journals/import/",
                "/admin/journals/categories/",
                "/admin/placements/",
                "/admin/layout-slots/",
                "/admin/system-category-placements/",
                "/admin/managed-navigation/",
                "/admin/navigationitem/",
            },
            "readonly": {
                "/admin/articles/",
                "/admin/article-review/",
                "/admin/journals/",
                "/admin/journals/import/",
                "/admin/journals/categories/",
                "/admin/placements/",
                "/admin/layout-slots/",
                "/admin/system-category-placements/",
            },
        }
        for role_code, paths in allowed_paths.items():
            client = Client()
            client.force_login(
                self.make_staff_user(ROLE_DEFINITIONS[role_code]["display_name"])
            )
            for path in paths:
                with self.subTest(role=role_code, path=path):
                    self.assertEqual(client.get(path).status_code, 200)
        for role_code, paths in denied_paths.items():
            client = Client()
            client.force_login(
                self.make_staff_user(ROLE_DEFINITIONS[role_code]["display_name"])
            )
            for path in paths:
                with self.subTest(role=role_code, path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], "/admin/")

    def test_static_publish_readers_cannot_submit_a_publish_request(self):
        readonly_client = Client()
        readonly_client.force_login(
            self.make_staff_user(ROLE_DEFINITIONS["readonly"]["display_name"])
        )
        publisher_client = Client()
        publisher_client.force_login(
            self.make_staff_user(ROLE_DEFINITIONS["publisher"]["display_name"])
        )

        denied = readonly_client.post("/admin/static-publish/", {})
        allowed = publisher_client.post("/admin/static-publish/", {})

        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers["Location"], "/admin/")
        self.assertEqual(allowed.status_code, 200)

    def test_navigation_admin_enforces_read_and_write_boundaries(self):
        navigation = NavigationItem.objects.filter(is_core=True).first()
        operator_client = Client()
        operator_client.force_login(self.make_staff_user("站点运营"))
        readonly_client = Client()
        readonly_client.force_login(self.make_staff_user("只读人员"))
        super_client = Client()
        super_client.force_login(self.make_staff_user("超级管理员"))

        self.assertEqual(operator_client.get("/admin/navigationitem/").status_code, 200)
        self.assertEqual(readonly_client.get("/admin/navigationitem/").status_code, 200)
        self.assertEqual(
            operator_client.get(
                f"/admin/navigationitem/edit/{navigation.pk}/"
            ).status_code,
            302,
        )
        self.assertEqual(
            readonly_client.get("/admin/navigationitem/new/").status_code,
            302,
        )
        self.assertEqual(
            super_client.get("/admin/navigationitem/new/").status_code, 200
        )

    def test_audit_log_admin_is_read_only(self):
        log = AuditLog.record(
            action=AuditAction.PUBLISH,
            status=AuditStatus.SUCCESS,
            message="published",
        )
        client = Client()
        client.force_login(self.make_staff_user("只读人员"))
        self.assertEqual(client.get("/admin/auditlog/").status_code, 200)
        self.assertEqual(
            client.get(f"/admin/auditlog/inspect/{log.pk}/").status_code,
            200,
        )
        self.assertEqual(client.get("/admin/auditlog/new/").status_code, 302)
        self.assertEqual(
            client.get(f"/admin/auditlog/edit/{log.pk}/").status_code,
            302,
        )
        self.assertEqual(
            client.get(f"/admin/auditlog/delete/{log.pk}/").status_code,
            302,
        )

    def test_super_admin_group_receives_all_page_permissions(self):
        group = Group.objects.get(name="超级管理员")
        codenames = set(
            GroupPagePermission.objects.filter(group=group).values_list(
                "permission__codename", flat=True
            )
        )
        self.assertEqual(
            codenames,
            {
                "add_page",
                "bulk_delete_page",
                "change_page",
                "delete_page",
                "lock_page",
                "publish_page",
                "unlock_page",
                "view_page",
            },
        )

    def test_super_admin_group_receives_collection_permissions_for_media(self):
        group = Group.objects.get(name="\u8d85\u7ea7\u7ba1\u7406\u5458")
        permissions = set(
            GroupCollectionPermission.objects.filter(group=group).values_list(
                "permission__content_type__app_label",
                "permission__codename",
            )
        )
        self.assertTrue(
            {
                ("wagtailimages", "add_image"),
                ("wagtailimages", "change_image"),
                ("wagtailimages", "choose_image"),
                ("wagtailimages", "delete_image"),
                ("wagtaildocs", "add_document"),
                ("wagtaildocs", "change_document"),
                ("wagtaildocs", "choose_document"),
                ("wagtaildocs", "delete_document"),
            }.issubset(permissions)
        )

    def test_super_admin_group_can_enter_media_admin(self):
        client = Client()
        client.force_login(self.make_staff_user("\u8d85\u7ea7\u7ba1\u7406\u5458"))
        self.assertEqual(client.get("/admin/images/").status_code, 200)
        self.assertEqual(client.get("/admin/documents/").status_code, 200)

    def test_readonly_role_sees_audit_settings(self):
        labels = self.visible_settings_labels(self.make_staff_user("只读人员"))
        self.assertEqual(labels, {"导航基线", "角色权限预设", "审计日志"})

    def test_core_navigation_changes_require_explicit_permission(self):
        operator = self.make_staff_user("站点运营")
        administrator = self.user_model.objects.create_superuser(
            username="foundation-admin",
            password="test-password",
            email="admin@example.com",
        )
        policy = CoreNavigationPermissionPolicy(NavigationItem)
        self.assertFalse(policy.user_has_permission(operator, "change"))
        self.assertTrue(policy.user_has_permission(administrator, "change"))

    def test_audit_log_is_immutable(self):
        log = AuditLog.record(
            action=AuditAction.PUBLISH,
            status=AuditStatus.SUCCESS,
            message="test publish",
        )
        log.message = "mutated"
        with self.assertRaisesMessage(Exception, "审计日志创建后不可修改"):
            log.save()
        with self.assertRaisesMessage(Exception, "审计日志创建后不可删除"):
            log.delete()
