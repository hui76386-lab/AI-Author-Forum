from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from wagtail.models import Page

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.placements.category_services import sync_category_placements
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


class CategoryAdminPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.journal = Journal.objects.create(
            name="Admin Journal", slug="admin-journal", az_group="A"
        )
        cls.other_journal = Journal.objects.create(
            name="Other Journal", slug="other-journal", az_group="O"
        )
        cls.root = JournalCategory.objects.create(
            journal=cls.journal,
            name="Root",
            code="ROOT",
            slug="root",
            depth=1,
            path_cache="root",
        )
        cls.destination = JournalCategory.objects.create(
            journal=cls.journal,
            name="Destination",
            code="DEST",
            slug="destination",
            depth=1,
            path_cache="destination",
        )

    def user_for(self, role_code):
        username = f"{role_code}-{get_user_model().objects.count()}"
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username,
            password="test",
            is_staff=True,
        )
        if role_code == "super_admin":
            user.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        return user

    def test_without_selected_journal_does_not_load_any_category_tree(self):
        client = Client()
        client.force_login(self.user_for("super_admin"))
        with (
            patch("ai_author_forum.journals.category_admin._category_rows") as rows,
            patch("ai_author_forum.journals.category_admin.get_category_tree") as tree,
        ):
            response = client.get(reverse("journals_category_admin"))
        self.assertEqual(response.status_code, 200)
        rows.assert_not_called()
        tree.assert_not_called()
        self.assertContains(response, "120")

    def test_global_exception_filter_lists_all_journals_and_drills_down(self):
        disabled = JournalCategory.objects.create(
            journal=self.journal,
            name="Disabled Root",
            code="DISABLED-ROOT",
            slug="disabled-root",
            depth=1,
            path_cache="disabled-root",
            status="disabled",
        )
        archived = JournalCategory.objects.create(
            journal=self.journal,
            name="Archived Root",
            code="ARCHIVED-ROOT",
            slug="archived-root",
            depth=1,
            path_cache="archived-root",
            status="archived",
        )
        foreign_archived = JournalCategory.objects.create(
            journal=self.other_journal,
            name="Foreign Archived",
            code="FOREIGN-ARCHIVED",
            slug="foreign-archived",
            depth=1,
            path_cache="foreign-archived",
            status="archived",
        )
        foreign_active = JournalCategory.objects.create(
            journal=self.other_journal,
            name="Foreign Active",
            code="FOREIGN-ACTIVE",
            slug="foreign-active",
            depth=1,
            path_cache="foreign-active",
            status="active",
        )
        client = Client()
        client.force_login(self.user_for("super_admin"))

        global_response = client.get(
            reverse("journals_category_admin"), {"status": "exception"}
        )

        self.assertEqual(global_response.status_code, 200)
        self.assertIsNone(global_response.context["journal"])
        summary = {
            item.pk: item.exception_category_count
            for item in global_response.context["exception_journals"]
        }
        self.assertEqual(
            summary,
            {self.journal.pk: 2, self.other_journal.pk: 1},
        )
        self.assertContains(global_response, self.journal.name)
        self.assertContains(global_response, self.other_journal.name)
        self.assertContains(global_response, "2 个异常栏目")
        self.assertContains(global_response, "1 个异常栏目")
        self.assertContains(
            global_response,
            f"?journal={self.journal.pk}&amp;status=exception",
        )
        self.assertContains(
            global_response,
            f"?journal={self.other_journal.pk}&amp;status=exception",
        )

        detail_response = client.get(
            reverse("journals_category_admin"),
            {"journal": self.other_journal.pk, "status": "exception"},
        )

        self.assertEqual(detail_response.status_code, 200)
        visible_rows = {
            item.pk
            for item in detail_response.context["categories"]
            if item.search_visible
        }
        self.assertEqual(visible_rows, {foreign_archived.pk})
        self.assertContains(detail_response, foreign_archived.name)
        self.assertNotContains(detail_response, foreign_active.name)
        self.assertNotIn(disabled.pk, visible_rows)
        self.assertNotIn(archived.pk, visible_rows)

    def test_content_manager_cannot_view_or_change_journal_categories(self):
        client = Client()
        client.force_login(self.user_for("content_manager"))

        response = client.get(reverse("journals_category_admin"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/admin/login/"))

        response = client.post(
            reverse("journals_category_admin"),
            {
                "journal": self.journal.pk,
                "operation": "save_category",
                "category-parent": "",
                "category-name": "Created",
                "category-code": "CREATED",
                "category-slug": "created",
                "category-description": "",
                "category-seo_title": "",
                "category-search_description": "",
                "category-sort_order": 0,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/admin/login/"))
        self.assertFalse(JournalCategory.objects.filter(code="CREATED").exists())

    def test_content_manager_cannot_move_or_change_status(self):
        client = Client()
        user = self.user_for("content_manager")
        self.assertFalse(
            user.has_perm("journals.move_journalcategory"),
            sorted(user.get_all_permissions()),
        )
        self.assertFalse(
            user.has_perm("journals.change_category_status"),
            sorted(user.get_all_permissions()),
        )
        client.force_login(user)
        move = client.post(
            reverse("journals_category_admin"),
            {
                "journal": self.journal.pk,
                "operation": "move_category",
                "move-category_id": self.root.pk,
                "move-new_parent_id": self.destination.pk,
                "move-expected_version": self.root.version,
                "move-confirmed": "on",
            },
        )
        self.assertEqual(move.status_code, 302)
        self.assertTrue(move.headers["Location"].startswith("/admin/login/"))
        status = client.post(
            reverse("journals_category_admin"),
            {
                "journal": self.journal.pk,
                "operation": "change_status",
                "status-category_id": self.root.pk,
                "status-new_status": "disabled",
                "status-confirmed": "on",
            },
        )
        self.assertEqual(status.status_code, 302)
        self.assertTrue(status.headers["Location"].startswith("/admin/login/"))
        self.root.refresh_from_db()
        self.assertIsNone(self.root.parent_id)
        self.assertEqual(self.root.status, "active")

    def test_add_root_form_renders_when_journal_has_no_categories(self):
        empty_journal = Journal.objects.create(
            name="Empty Journal", slug="empty-journal", az_group="E"
        )
        user = get_user_model().objects.create_superuser(
            "empty-template-admin", "empty-template@example.com", "test"
        )
        grant_business_super_admin(user)
        client = Client()
        client.force_login(user)

        response = client.get(
            reverse("journals_category_admin"),
            {"journal": empty_journal.pk, "mode": "add_root"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_category"])
        self.assertContains(response, "\u65b0\u589e\u6839\u680f\u76ee")
        self.assertContains(response, "\u521b\u5efa\u680f\u76ee")
        self.assertContains(response, 'name="operation" value="save_category"')
        self.assertNotContains(response, "\u672a\u627e\u5230\u680f\u76ee")

    def test_workbench_template_renders_tree_detail_and_controlled_actions(self):
        user = get_user_model().objects.create_superuser(
            "template-admin", "template@example.com", "test"
        )
        grant_business_super_admin(user)
        client = Client()
        client.force_login(user)

        response = client.get(
            reverse("journals_category_admin"),
            {"journal": self.journal.pk, "selected": self.root.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "wagtailadmin/journals/categories.html")
        self.assertContains(response, "栏目树")
        self.assertContains(response, "基本信息与引用")
        self.assertContains(response, "影响预览基线")
        self.assertContains(response, "搜索栏目")
        self.assertContains(response, "全部状态")
        self.assertContains(response, "同级受控排序")
        self.assertContains(response, "不提供自由拖拽")
        self.assertContains(response, "批量状态操作")
        self.assertContains(response, "预览移动影响")
        self.assertContains(response, "预览状态影响")
        self.assertContains(response, "is-selected")

    def test_workbench_search_keeps_ancestor_and_hides_unmatched_branch(self):
        child = JournalCategory.objects.create(
            journal=self.journal,
            parent=self.root,
            name="Needle Child",
            code="NEEDLE",
            slug="needle-child",
            status="disabled",
            depth=2,
            path_cache="root/needle-child",
        )
        client = Client()
        client.force_login(self.user_for("super_admin"))

        response = client.get(
            reverse("journals_category_admin"),
            {
                "journal": self.journal.pk,
                "q": "needle",
                "status": "disabled",
                "selected": child.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.root.name)
        self.assertContains(response, child.name)
        self.assertNotContains(response, self.destination.name)
        rows = {item.pk: item for item in response.context["categories"]}
        self.assertTrue(rows[self.root.pk].search_visible)
        self.assertTrue(rows[self.root.pk].expanded)
        self.assertTrue(rows[child.pk].search_match)
        self.assertFalse(rows[self.destination.pk].search_visible)

    def test_readonly_user_cannot_view_journal_categories(self):
        client = Client()
        client.force_login(self.user_for("readonly"))

        response = client.get(
            reverse("journals_category_admin"),
            {"journal": self.journal.pk, "selected": self.root.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/admin/login/"))

    def test_superuser_move_requires_confirmation_before_write(self):
        user = get_user_model().objects.create_superuser(
            "root-admin", "root@example.com", "test"
        )
        grant_business_super_admin(user)
        client = Client()
        client.force_login(user)
        payload = {
            "journal": self.journal.pk,
            "operation": "move_category",
            "move-category_id": self.root.pk,
            "move-new_parent_id": self.destination.pk,
            "move-expected_version": self.root.version,
        }
        preview = client.post(reverse("journals_category_admin"), payload)
        self.assertEqual(preview.status_code, 200)
        self.root.refresh_from_db()
        self.assertIsNone(self.root.parent_id)
        self.assertEqual(preview.context["confirmation"]["kind"], "move")
        payload["move-confirmed"] = "on"
        applied = client.post(reverse("journals_category_admin"), payload)
        self.assertEqual(applied.status_code, 302)
        self.root.refresh_from_db()
        self.assertEqual(self.root.parent_id, self.destination.pk)


class CategorySelectorAndSystemPlacementAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "selector-admin", "selector@example.com", "test"
        )
        grant_business_super_admin(self.user)
        self.journal = Journal.objects.create(
            name="Selector Journal", slug="selector-journal", az_group="S"
        )
        self.other = Journal.objects.create(
            name="Other Selector", slug="other-selector", az_group="O"
        )
        self.active = JournalCategory.objects.create(
            journal=self.journal,
            name="Active",
            code="ACTIVE",
            slug="active",
            depth=1,
            path_cache="active",
        )
        self.hidden = JournalCategory.objects.create(
            journal=self.journal,
            name="Hidden",
            code="HIDDEN",
            slug="hidden",
            depth=1,
            path_cache="hidden",
            status="hidden",
        )
        self.disabled = JournalCategory.objects.create(
            journal=self.journal,
            name="Disabled",
            code="DISABLED",
            slug="disabled",
            depth=1,
            path_cache="disabled",
            status="disabled",
        )
        self.foreign = JournalCategory.objects.create(
            journal=self.other,
            name="Foreign",
            code="FOREIGN",
            slug="foreign",
            depth=1,
            path_cache="foreign",
        )
        self.client.force_login(self.user)

    def create_live_article(self):
        article = ArticlePage(
            title="System placement article",
            slug="system-placement-article",
            static_slug="system-placement-article",
            abstract="Abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=self.journal,
        )
        Page.get_first_root_node().add_child(instance=article)
        ArticleCategoryAssignment.objects.create(
            article=article, category=self.active, is_primary=True
        )
        formally_approve_test_article(article, actor=self.user)
        sync_category_placements(article_id=article.pk, actor=self.user)
        article.refresh_from_db()
        return article

    def test_category_options_are_partitioned_and_status_filtered(self):
        response = self.client.get(
            reverse("article_admin:category_options"), {"journal": self.journal.pk}
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["categories"]}
        self.assertEqual(ids, {self.active.pk, self.hidden.pk})
        labels = {item["label"] for item in response.json()["categories"]}
        self.assertIn("Active [ACTIVE]", labels)

    def test_category_options_reject_user_without_article_access(self):
        unauthorized = get_user_model().objects.create_user(
            "selector-unauthorized",
            email="selector-unauthorized@example.com",
            display_name="Selector Unauthorized",
            password="test",
            is_staff=True,
        )
        unauthorized.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin", codename="access_admin"
            )
        )
        client = Client()
        client.force_login(unauthorized)
        response = client.get(
            reverse("article_admin:category_options"), {"journal": self.journal.pk}
        )
        self.assertEqual(response.status_code, 404)

    def test_system_placement_page_is_super_admin_only(self):
        article = self.create_live_article()
        viewer = get_user_model().objects.create_user(
            "placement-viewer",
            email="placement-viewer@example.com",
            display_name="Placement Viewer",
            password="test",
            is_staff=True,
        )
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin", codename="access_admin"
            ),
            Permission.objects.get(
                content_type__app_label="placements",
                codename="view_system_categoryplacement",
            ),
        )
        client = Client()
        client.force_login(viewer)
        page = client.get(reverse("system-category-placements:index"))
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["Location"], "/admin/")
        denied = client.post(
            reverse("system-category-placements:index"), {"article_id": article.pk}
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers["Location"], "/admin/")
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="placements",
                codename="retry_categoryplacement_sync",
            )
        )
        still_denied = client.post(
            reverse("system-category-placements:index"), {"article_id": article.pk}
        )
        self.assertEqual(still_denied.status_code, 302)
        self.assertEqual(still_denied.headers["Location"], "/admin/")
        self.assertEqual(
            ArticlePlacement.objects.filter(
                article=article, source="system", target_category=self.active
            ).count(),
            1,
        )
