from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail.admin.menu import admin_menu
from wagtail.models import GroupPagePermission

from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalCategoryStatus,
    JournalStatus,
)
from ai_author_forum.site_settings.dashboard import (
    RoleDashboardPanel,
    get_role_dashboard_context,
)
from ai_author_forum.site_settings.management.commands.seed_roles import (
    ROLE_DEFINITIONS,
)
from ai_author_forum.site_settings.middleware import (
    AdminNavigationPreviewFrameOptionsMiddleware,
)
from ai_author_forum.site_settings.models import AdminRolePreset
from ai_author_forum.site_settings.permissions import get_admin_permission_context
from ai_author_forum.site_settings.wagtail_hooks import add_role_dashboard_panel


class AdminMaturityPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()

    def make_staff_user(self, role_code):
        definition = ROLE_DEFINITIONS[role_code]
        user = self.user_model.objects.create_user(
            username=f"{role_code}-{self.user_model.objects.count()}",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=definition["display_name"]))
        return user

    def test_seeded_roles_match_fine_grained_permission_matrix(self):
        content = get_admin_permission_context(self.make_staff_user("content_manager"))
        self.assertTrue(content["can_edit_article"])
        self.assertTrue(content["can_manage_placement"])
        self.assertFalse(content["can_review_article"])
        self.assertFalse(content["can_add_journal"])
        self.assertFalse(content["can_import_journals"])
        self.assertFalse(content["can_publish_static"])

        reviewer = get_admin_permission_context(self.make_staff_user("reviewer"))
        self.assertTrue(reviewer["can_review_article"])
        self.assertFalse(reviewer["can_edit_article"])
        self.assertFalse(reviewer["can_manage_placement"])

        operator_user = self.make_staff_user("site_operator")
        operator = get_admin_permission_context(operator_user)
        self.assertTrue(operator["can_add_journal"])
        self.assertTrue(operator["can_change_journal"])
        self.assertTrue(operator_user.has_perm("images.add_customimage"))
        self.assertFalse(operator["can_import_journals"])
        self.assertFalse(operator["can_edit_article"])
        self.assertFalse(operator["can_publish_static"])

        publisher = get_admin_permission_context(self.make_staff_user("publisher"))
        self.assertTrue(publisher["can_publish_static"])
        self.assertTrue(publisher["can_retry_publish"])
        self.assertTrue(publisher["can_rollback_publish"])
        self.assertFalse(publisher["can_edit_article"])

        readonly = get_admin_permission_context(self.make_staff_user("readonly"))
        self.assertTrue(readonly["is_readonly_dashboard"])
        self.assertTrue(readonly["can_view_static_publish"])
        self.assertTrue(readonly["can_view_audit_log"])
        self.assertFalse(readonly["has_write_capability"])

    def test_project_lead_and_super_admin_receive_all_action_flags(self):
        action_flags = {
            "can_add_journal",
            "can_change_journal",
            "can_import_journals",
            "can_edit_article",
            "can_review_article",
            "can_manage_placement",
            "can_publish_static",
            "can_retry_publish",
            "can_rollback_publish",
        }
        for role_code in ("project_lead", "super_admin"):
            with self.subTest(role=role_code):
                flags = get_admin_permission_context(self.make_staff_user(role_code))
                self.assertTrue(all(flags[name] for name in action_flags))

    def test_seed_roles_is_idempotent_for_groups_permissions_and_presets(self):
        role_names = {
            definition["display_name"] for definition in ROLE_DEFINITIONS.values()
        }

        def snapshot():
            groups = Group.objects.filter(name__in=role_names).order_by("name")
            return {
                "group_count": groups.count(),
                "group_permissions": {
                    group.name: tuple(
                        group.permissions.order_by("pk").values_list("pk", flat=True)
                    )
                    for group in groups
                },
                "page_permissions": tuple(
                    GroupPagePermission.objects.filter(group__name__in=role_names)
                    .order_by("group__name", "page_id", "permission_id")
                    .values_list("group__name", "page_id", "permission_id")
                ),
                "presets": tuple(
                    AdminRolePreset.objects.filter(role_code__in=ROLE_DEFINITIONS)
                    .order_by("role_code")
                    .values_list("role_code", "group_id", "is_active", "is_system")
                ),
            }

        before = snapshot()
        call_command("seed_roles", verbosity=0)
        after = snapshot()
        self.assertEqual(after, before)
        self.assertEqual(after["group_count"], len(ROLE_DEFINITIONS))


class RoleDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.factory = RequestFactory()

    def make_staff_user(self, role_code):
        definition = ROLE_DEFINITIONS[role_code]
        user = self.user_model.objects.create_user(
            username=f"dashboard-{role_code}-{self.user_model.objects.count()}",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=definition["display_name"]))
        return user

    def section_codes(self, user):
        return {
            section["code"]
            for section in get_role_dashboard_context(user)["dashboard_sections"]
        }

    def test_dashboard_sections_are_selected_by_effective_permissions(self):
        expected = {
            "content_manager": {"content", "operations"},
            "reviewer": {"review"},
            "site_operator": {"operations"},
            "publisher": {"publishing"},
            "readonly": {"readonly"},
        }
        actual = {
            role_code: self.section_codes(self.make_staff_user(role_code))
            for role_code in expected
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len({frozenset(value) for value in actual.values()}), 5)

    def test_dashboard_does_not_depend_on_group_name(self):
        user = self.make_staff_user("publisher")
        user.groups.clear()
        custom_group = Group.objects.create(name="custom-dashboard-publisher")
        source_group = Group.objects.get(
            name=ROLE_DEFINITIONS["publisher"]["display_name"]
        )
        custom_group.permissions.set(source_group.permissions.all())
        user.groups.add(custom_group)
        user = self.user_model.objects.get(pk=user.pk)

        self.assertEqual(self.section_codes(user), {"publishing"})

    def test_all_dashboard_metrics_link_to_admin_lists_with_filters(self):
        for role_code in (
            "content_manager",
            "reviewer",
            "site_operator",
            "publisher",
            "readonly",
        ):
            context = get_role_dashboard_context(self.make_staff_user(role_code))
            for section in context["dashboard_sections"]:
                for metric in section["metrics"]:
                    with self.subTest(role=role_code, metric=metric["label"]):
                        parsed = urlsplit(metric["url"])
                        self.assertTrue(parsed.path.startswith("/admin/"))
                        self.assertTrue(parse_qs(parsed.query))

    def test_dashboard_links_use_filters_supported_by_target_lists(self):
        content = get_role_dashboard_context(self.make_staff_user("content_manager"))[
            "dashboard_sections"
        ][0]
        content_queries = [
            parse_qs(urlsplit(metric["url"]).query) for metric in content["metrics"]
        ]
        self.assertEqual(
            set(content_queries[0]),
            {"review_status", "owner"},
        )
        self.assertEqual(
            set(content_queries[3]),
            {"owner", "updated_from"},
        )

        review = get_role_dashboard_context(self.make_staff_user("reviewer"))[
            "dashboard_sections"
        ][0]
        review_queries = [
            parse_qs(urlsplit(metric["url"]).query) for metric in review["metrics"]
        ]
        self.assertEqual(review_queries[1]["waiting"], ["48h"])
        self.assertEqual(set(review_queries[2]), {"reviewed_by", "reviewed_from"})

        publishing = get_role_dashboard_context(self.make_staff_user("publisher"))[
            "dashboard_sections"
        ][0]
        publishing_queries = [
            parse_qs(urlsplit(metric["url"]).query) for metric in publishing["metrics"]
        ]
        self.assertEqual(set(publishing_queries[0]), {"created_from"})
        self.assertEqual(publishing_queries[1]["status"], ["failed"])
        self.assertEqual(publishing_queries[2]["target_status"], ["failed"])
        self.assertEqual(publishing_queries[3]["manifest_status"], ["active"])
        self.assertEqual(publishing_queries[4]["manifest_status"], ["rollback"])

        readonly = get_role_dashboard_context(self.make_staff_user("readonly"))[
            "dashboard_sections"
        ][0]
        readonly_queries = [
            parse_qs(urlsplit(metric["url"]).query) for metric in readonly["metrics"]
        ]
        self.assertEqual(readonly_queries[0]["status"], ["pending"])
        self.assertEqual(readonly_queries[1]["status"], ["failed"])
        self.assertEqual(readonly_queries[-1]["status"], ["failure"])

    def test_category_anomaly_metric_aggregates_all_journals_and_uses_global_filter(
        self,
    ):
        first = Journal.objects.create(
            name="Anomaly Journal A",
            slug="anomaly-journal-a",
            az_group="A",
            sort_order=1,
        )
        second = Journal.objects.create(
            name="Anomaly Journal B",
            slug="anomaly-journal-b",
            az_group="A",
            sort_order=2,
        )
        for journal, code, status in (
            (first, "DISABLED-A", JournalCategoryStatus.DISABLED),
            (first, "ARCHIVED-A", JournalCategoryStatus.ARCHIVED),
            (second, "DISABLED-B", JournalCategoryStatus.DISABLED),
        ):
            JournalCategory.objects.create(
                journal=journal,
                name=code,
                code=code,
                slug=code.lower(),
                depth=1,
                path_cache=code.lower(),
                status=status,
            )
        JournalCategory.objects.create(
            journal=second,
            name="Active B",
            code="ACTIVE-B",
            slug="active-b",
            depth=1,
            path_cache="active-b",
            status=JournalCategoryStatus.ACTIVE,
        )

        operations = next(
            section
            for section in get_role_dashboard_context(
                self.make_staff_user("super_admin")
            )["dashboard_sections"]
            if section["code"] == "operations"
        )
        metric = next(
            item for item in operations["metrics"] if item["label"] == "栏目异常"
        )
        parsed = urlsplit(metric["url"])

        self.assertEqual(metric["value"], 3)
        self.assertEqual(parsed.path, reverse("journals_category_admin"))
        self.assertEqual(parse_qs(parsed.query), {"status": ["exception"]})
        self.assertNotIn("journal", parse_qs(parsed.query))

    def test_site_operator_dashboard_hides_category_links_without_category_permission(
        self,
    ):
        context = get_role_dashboard_context(self.make_staff_user("site_operator"))
        operations = next(
            section
            for section in context["dashboard_sections"]
            if section["code"] == "operations"
        )
        self.assertNotIn(
            reverse("journals_category_admin"),
            [metric["url"].split("?", 1)[0] for metric in operations["metrics"]],
        )
        journal_card = next(
            card for card in context["workspace_cards"] if card["code"] == "journals"
        )
        self.assertNotIn(
            reverse("journals_category_admin"),
            [link["url"] for link in journal_card["links"]],
        )

    def test_readonly_panel_contains_no_write_operations(self):
        user = self.make_staff_user("readonly")
        request = self.factory.get("/admin/")
        request.user = user
        html = RoleDashboardPanel().render_html({"request": request})

        self.assertIn('data-readonly="true"', html)
        self.assertIn('data-dashboard-section="readonly"', html)
        self.assertNotIn("<form", html)
        self.assertNotIn("发起发布", html)
        self.assertNotIn("重试发布", html)
        self.assertNotIn("回滚发布", html)

    def test_dashboard_query_count_is_bounded_with_152_journals(self):
        Journal.objects.bulk_create(
            [
                Journal(
                    name=f"Journal {index:03d}",
                    slug=f"journal-{index:03d}",
                    az_group="J",
                    status=(
                        JournalStatus.ACTIVE if index % 2 else JournalStatus.PAUSED
                    ),
                )
                for index in range(152)
            ]
        )
        user = self.make_staff_user("site_operator")
        user = self.user_model.objects.get(pk=user.pk)

        with CaptureQueriesContext(connection) as queries:
            context = get_role_dashboard_context(user)

        self.assertEqual(
            {section["code"] for section in context["dashboard_sections"]},
            {"operations"},
        )
        self.assertLessEqual(len(queries), 8)
        self.assertEqual(
            len(
                [
                    metric
                    for section in context["dashboard_sections"]
                    for metric in section["metrics"]
                ]
            ),
            2,
        )


class AdminNavigationPreviewFrameOptionsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = AdminNavigationPreviewFrameOptionsMiddleware(
            lambda request: HttpResponse("preview")
        )

    def test_staff_preview_is_same_origin_frameable(self):
        request = self.factory.get("/?admin_navigation_preview=1")
        request.user = get_user_model().objects.create_user(
            username="preview-staff",
            password="test-password",
            is_staff=True,
        )

        response = self.middleware(request)

        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_nonstaff_or_unmarked_requests_do_not_override_frame_policy(self):
        user = get_user_model().objects.create_user(
            username="preview-nonstaff",
            password="test-password",
        )
        requests = (
            self.factory.get("/?admin_navigation_preview=1"),
            self.factory.get("/"),
            self.factory.get("/articles/example/?admin_navigation_preview=1"),
        )
        for request in requests:
            with self.subTest(path=request.get_full_path()):
                request.user = user
                response = self.middleware(request)
                self.assertNotIn("X-Frame-Options", response)


class BusinessNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()
        cls.factory = RequestFactory()

    def make_staff_user(self, role_code):
        definition = ROLE_DEFINITIONS[role_code]
        user = self.user_model.objects.create_user(
            username=f"navigation-{role_code}-{self.user_model.objects.count()}",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=definition["display_name"]))
        return user

    def menu_structure(self, user):
        request = self.factory.get("/admin/")
        request.user = user
        structure = {}
        for item in admin_menu.menu_items_for_request(request):
            children = []
            if hasattr(item, "menu"):
                children = [
                    child.name for child in item.menu.menu_items_for_request(request)
                ]
            structure[item.name] = children
        return structure

    def business_children(self, user):
        structure = self.menu_structure(user)
        return {
            child
            for domain in (
                "business-content",
                "business-journals-domain",
                "business-delivery",
                "business-assets",
            )
            for child in structure.get(domain, [])
        }

    def rendered_sidebar_names(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("wagtailadmin_home"))
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<script id="wagtail-sidebar-props" type="application/json">(.*?)</script>',
            response.content.decode("utf-8"),
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        sidebar = json.loads(match.group(1))
        main_menu = next(
            module
            for module in sidebar["modules"]
            if module["_type"] == "wagtail.sidebar.MainMenuModule"
        )
        return [item["_args"][0]["name"] for item in main_menu["_args"][0]]

    def test_superuser_sees_four_business_domains_without_flat_technical_entries(self):
        user = self.user_model.objects.create_superuser(
            username="navigation-superuser",
            password="test-password",
            email="superuser@example.com",
        )
        structure = self.menu_structure(user)

        self.assertTrue(
            {
                "business-content",
                "business-journals-domain",
                "business-delivery",
                "business-assets",
            }.issubset(structure)
        )
        self.assertTrue(
            all(structure[name] for name in structure if name.startswith("business-"))
        )
        self.assertTrue(
            {"explorer", "snippets", "all-articles", "pending-articles"}.isdisjoint(
                structure
            )
        )

    def rendered_sidebar_links(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("wagtailadmin_home"))
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<script id="wagtail-sidebar-props" type="application/json">(.*?)</script>',
            response.content.decode("utf-8"),
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        sidebar = json.loads(match.group(1))
        links = {}

        def collect(value):
            if isinstance(value, list):
                for child in value:
                    collect(child)
            elif isinstance(value, dict):
                if value.get("_type") == "wagtail.sidebar.LinkMenuItem":
                    payload = value["_args"][0]
                    if isinstance(payload["name"], str):
                        links[payload["name"]] = payload["url"]
                for child in value.values():
                    collect(child)

        collect(sidebar)
        return links

    def test_navigation_entry_opens_a_scope_the_role_can_access(self):
        expected_urls = {
            "site_operator": "/admin/managed-navigation/",
            "readonly": "/admin/managed-navigation/",
        }
        for role_code, expected_url in expected_urls.items():
            with self.subTest(role=role_code):
                user = self.make_staff_user(role_code)
                links = self.rendered_sidebar_links(user)
                self.assertEqual(links["business-navigation"], expected_url)
                response = self.client.get(expected_url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "wagtailadmin/navigation/manage.html")

        for role_code in ("content_manager", "reviewer", "publisher"):
            with self.subTest(role=role_code):
                links = self.rendered_sidebar_links(self.make_staff_user(role_code))
                self.assertNotIn("business-navigation", links)

    def test_site_settings_entry_matches_the_role_write_capability(self):
        operator = self.make_staff_user("site_operator")
        operator_links = self.rendered_sidebar_links(operator)
        self.assertEqual(
            operator_links["business-site-settings"],
            "/admin/settings/site_settings/sitesettings/",
        )

        readonly = self.make_staff_user("readonly")
        readonly_links = self.rendered_sidebar_links(readonly)
        self.assertEqual(
            readonly_links["business-site-settings"],
            reverse("site_settings_summary"),
        )

        response = self.client.get(readonly_links["business-site-settings"])
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "wagtailadmin/site_settings/summary.html")
        html = response.content.decode("utf-8")
        self.assertIn('data-readonly="true"', html)
        self.assertNotIn('<form method="post"', html.lower())
        self.assertNotIn("/admin/settings/site_settings/sitesettings/", html)

    def test_site_settings_summary_rejects_roles_without_view_access(self):
        user = self.make_staff_user("content_manager")
        self.client.force_login(user)

        response = self.client.get(reverse("site_settings_summary"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("wagtailadmin_home"))
        self.assertNotIn("business-site-settings", self.rendered_sidebar_links(user))

    def test_rendered_sidebar_removes_original_flat_business_entries(self):
        user = self.user_model.objects.create_superuser(
            username="rendered-navigation-superuser",
            password="test-password",
            email="rendered-navigation@example.com",
        )
        names = self.rendered_sidebar_names(user)

        self.assertTrue(
            {
                "business-content",
                "business-journals-domain",
                "business-delivery",
                "business-assets",
            }.issubset(names)
        )
        self.assertTrue(
            {
                "managed-navigation",
                "journals",
                "article-review",
                "placements",
                "layout-slots",
                "system-category-placements",
                "static-publish",
            }.isdisjoint(names)
        )

    def test_business_roles_only_receive_reachable_role_specific_entries(self):
        expected_entries = {
            "content_manager": {
                "business-homepage-composition",
                "business-articles",
                "business-placement-errors",
                "business-placements",
                "business-layout-slots",
                "business-system-placements",
            },
            "reviewer": {"business-review"},
            "site_operator": {
                "business-journals",
                "business-navigation",
                "business-site-settings",
                "business-content-readiness",
            },
            "publisher": {
                "business-static-publish",
                "business-content-readiness",
                "business-audit-log",
            },
            "readonly": {
                "business-static-publish",
                "business-navigation",
                "business-site-settings",
                "business-content-readiness",
                "business-audit-log",
            },
        }
        for role_code, expected in expected_entries.items():
            with self.subTest(role=role_code):
                self.assertEqual(
                    self.business_children(self.make_staff_user(role_code)), expected
                )

    def test_constructed_business_domains_never_render_empty_submenus(self):
        for role_code in (
            "content_manager",
            "reviewer",
            "site_operator",
            "publisher",
            "readonly",
        ):
            with self.subTest(role=role_code):
                structure = self.menu_structure(self.make_staff_user(role_code))
                for name, children in structure.items():
                    if name.startswith("business-"):
                        self.assertTrue(children)

    def test_business_homepage_replaces_generic_panels_with_role_workspace(self):
        for role_code in (
            "content_manager",
            "reviewer",
            "site_operator",
            "publisher",
            "readonly",
        ):
            with self.subTest(role=role_code):
                request = self.factory.get("/admin/")
                request.user = self.make_staff_user(role_code)
                panels = [object(), object()]

                add_role_dashboard_panel(request, panels)

                self.assertEqual(len(panels), 1)
                self.assertIsInstance(panels[0], RoleDashboardPanel)

    def test_workflow_and_workspace_links_follow_the_five_step_permission_model(self):
        expected_availability = {
            "content_manager": [False, True, False, True, False],
            "reviewer": [False, False, True, False, False],
            "site_operator": [True, False, False, False, False],
            "publisher": [False, False, False, False, True],
            "readonly": [False, False, False, False, True],
        }
        for role_code, availability in expected_availability.items():
            with self.subTest(role=role_code):
                context = get_role_dashboard_context(self.make_staff_user(role_code))
                self.assertEqual(
                    [step["title"] for step in context["workflow_steps"]],
                    ["子期刊", "文章", "审核", "投放", "静态发布"],
                )
                self.assertEqual(
                    [step["is_available"] for step in context["workflow_steps"]],
                    availability,
                )

        reviewer_context = get_role_dashboard_context(self.make_staff_user("reviewer"))
        reviewer_card = next(
            card
            for card in reviewer_context["workspace_cards"]
            if card["code"] == "articles"
        )
        self.assertEqual(
            [link["label"] for link in reviewer_card["links"]],
            ["待审核"],
        )
        self.assertEqual(
            [action["label"] for action in reviewer_context["quick_actions"]],
            ["处理待审核文章"],
        )

        readonly_context = get_role_dashboard_context(self.make_staff_user("readonly"))
        self.assertEqual(readonly_context["quick_actions"], [])
