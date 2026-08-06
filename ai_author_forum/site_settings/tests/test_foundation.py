import importlib

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from wagtail.models import GroupCollectionPermission, GroupPagePermission

from ai_author_forum.site_settings.admin_views import CoreNavigationPermissionPolicy
from ai_author_forum.site_settings.management.commands.seed_roles import (
    JOURNAL_EDITOR_ACCESS_GROUP_NAME,
    ROLE_DEFINITIONS,
)
from ai_author_forum.site_settings.models import (
    AdminRolePreset,
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

    def make_user(self, username, *, group="", is_superuser=False):
        user = self.user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username,
            password="test-password",
            is_staff=True,
            is_superuser=is_superuser,
        )
        if group:
            user.groups.add(Group.objects.get(name=group))
        return user

    def test_seed_roles_contains_only_business_super_admin_and_technical_group(self):
        self.assertEqual(set(ROLE_DEFINITIONS), {"super_admin"})
        self.assertTrue(Group.objects.filter(name="超级管理员").exists())
        self.assertTrue(
            Group.objects.filter(name=JOURNAL_EDITOR_ACCESS_GROUP_NAME).exists()
        )
        self.assertFalse(
            AdminRolePreset.objects.exclude(role_code="super_admin")
            .filter(is_active=True)
            .exists()
        )

    def test_seed_roles_is_idempotent(self):
        group_snapshot = {
            group.name: tuple(
                group.permissions.order_by("pk").values_list("pk", flat=True)
            )
            for group in Group.objects.filter(
                name__in=("超级管理员", JOURNAL_EDITOR_ACCESS_GROUP_NAME)
            )
        }
        page_snapshot = tuple(
            GroupPagePermission.objects.filter(
                group__name__in=("超级管理员", JOURNAL_EDITOR_ACCESS_GROUP_NAME)
            )
            .order_by("group_id", "permission_id")
            .values_list("group_id", "page_id", "permission_id")
        )
        call_command("seed_roles", verbosity=0)
        self.assertEqual(
            group_snapshot,
            {
                group.name: tuple(
                    group.permissions.order_by("pk").values_list("pk", flat=True)
                )
                for group in Group.objects.filter(
                    name__in=("超级管理员", JOURNAL_EDITOR_ACCESS_GROUP_NAME)
                )
            },
        )
        self.assertEqual(
            page_snapshot,
            tuple(
                GroupPagePermission.objects.filter(
                    group__name__in=("超级管理员", JOURNAL_EDITOR_ACCESS_GROUP_NAME)
                )
                .order_by("group_id", "permission_id")
                .values_list("group_id", "page_id", "permission_id")
            ),
        )

    def test_business_super_admin_can_enter_global_admin_modules(self):
        user = self.make_user("business-admin", group="超级管理员")
        client = Client()
        client.force_login(user)
        for path in (
            "/admin/",
            "/admin/accounts/",
            "/admin/journals/",
            "/admin/articles/",
            "/admin/placements/",
            "/admin/static-publish/",
            "/admin/auditlog/",
            "/admin/images/",
            "/admin/documents/",
        ):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_bare_django_superuser_is_not_a_business_authorization_bypass(self):
        user = self.make_user("recovery-only", is_superuser=True)
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get("/admin/accounts/").status_code, 403)
        policy = CoreNavigationPermissionPolicy(NavigationItem)
        self.assertFalse(policy.user_has_permission(user, "change"))

    def test_super_admin_group_receives_page_and_media_permissions(self):
        group = Group.objects.get(name="超级管理员")
        page_codenames = set(
            GroupPagePermission.objects.filter(group=group).values_list(
                "permission__codename", flat=True
            )
        )
        self.assertEqual(
            page_codenames,
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
        media_permissions = set(
            GroupCollectionPermission.objects.filter(group=group).values_list(
                "permission__content_type__app_label",
                "permission__codename",
            )
        )
        self.assertTrue(
            {
                ("wagtailimages", "add_image"),
                ("wagtailimages", "change_image"),
                ("wagtaildocs", "add_document"),
                ("wagtaildocs", "change_document"),
            }.issubset(media_permissions)
        )

    def test_editor_technical_group_has_no_global_business_change_permissions(self):
        group = Group.objects.get(name=JOURNAL_EDITOR_ACCESS_GROUP_NAME)
        permissions = set(
            group.permissions.values_list("content_type__app_label", "codename")
        )
        self.assertIn(("wagtailadmin", "access_admin"), permissions)
        self.assertNotIn(("journals", "change_journal"), permissions)
        self.assertNotIn(("articles", "change_articlepage"), permissions)
        self.assertNotIn(("articles", "review_article"), permissions)
        self.assertNotIn(("static_publish", "publish_static_site"), permissions)

    def test_audit_log_is_immutable_through_model_and_queryset(self):
        log = AuditLog.record(
            action=AuditAction.PUBLISH,
            status=AuditStatus.SUCCESS,
            message="test publish",
        )
        log.message = "mutated"
        with self.assertRaisesMessage(ValidationError, "审计日志创建后不可修改"):
            log.save()
        with self.assertRaisesMessage(ValidationError, "审计日志创建后不可删除"):
            log.delete()
        with self.assertRaisesMessage(ValidationError, "审计日志创建后不可修改"):
            AuditLog.objects.filter(pk=log.pk).update(message="mutated")
        with self.assertRaisesMessage(ValidationError, "审计日志创建后不可删除"):
            AuditLog.objects.filter(pk=log.pk).delete()
