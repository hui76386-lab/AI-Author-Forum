from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from wagtail.snippets.models import get_snippet_models

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.site_settings.management.commands.seed_roles import (
    ROLE_DEFINITIONS,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)

from ..forms import make_target_value
from ..models import ArticlePlacement, LayoutSlot


class LayoutSlotDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = HomePage.objects.first()
        cls.news_listing = NewsListingPage(
            title="Slot dashboard articles",
            slug="slot-dashboard-articles",
            introduction="Slot dashboard test articles.",
        )
        cls.home.add_child(instance=cls.news_listing)
        cls.news_listing.save_revision().publish()
        cls.journal = Journal.objects.create(
            name="Slot Journal",
            name_cn="版位测试期刊",
            slug="slot-dashboard-journal",
            az_group="S",
            status="active",
        )
        cls.superuser = grant_business_super_admin(
            get_user_model().objects.create_superuser(
                "slot-admin", "slot-admin@example.com", "password"
            )
        )
        cls.article_a = cls.create_article("Slot article A", "slot-article-a")
        cls.article_b = cls.create_article("Slot article B", "slot-article-b")

    @classmethod
    def create_article(cls, title, slug):
        article = ArticlePage(
            title=title,
            slug=slug,
            abstract=f"{title} abstract.",
            body=[{"type": "paragraph", "value": f"{title} body."}],
            authors="Editor",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            primary_journal=cls.journal,
            keywords="ai",
            static_slug=slug,
        )
        cls.news_listing.add_child(instance=article)
        article.save_revision().publish()
        formally_approve_test_article(article, actor=cls.superuser)
        return ArticlePage.objects.get(pk=article.pk)

    def setUp(self):
        self.client.force_login(self.superuser)

    def slot_payload(self, slot, **overrides):
        payload = {
            "slot_id": slot.pk,
            "target": make_target_value(ArticlePlacement.TargetType.MAIN_SITE),
            "title": slot.title,
            "max_items": slot.max_items,
            "fill_mode": slot.fill_mode,
            "description": slot.description,
            "is_active": "on" if slot.is_active else "",
            "sort_order": slot.sort_order,
        }
        payload.update(overrides)
        return payload

    def test_dashboard_manages_home_section_and_journal_slots(self):
        cases = (
            (LayoutSlot.Scope.HOME, "home_hero"),
            (LayoutSlot.Scope.SECTION, "section_top_story"),
            (LayoutSlot.Scope.JOURNAL, "journal_hero"),
        )
        for scope, slot_code in cases:
            with self.subTest(scope=scope):
                response = self.client.get(
                    reverse("layout-slots:index"), data={"scope": scope}
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-slot-code="{slot_code}"')
                self.assertEqual(response.context["selected_scope"], scope)

        response = self.client.get(reverse("layout-slots:index"))
        self.assertEqual(
            tuple(response.context["slot_form"].fields),
            (
                "title",
                "max_items",
                "fill_mode",
                "description",
                "is_active",
                "sort_order",
            ),
        )
        self.assertContains(response, "不提供新增模板结构、改变布局或自由拖拽能力")
        self.assertNotIn(LayoutSlot, get_snippet_models())

    def test_dashboard_active_filter_limits_scope_slots_and_selected_slot(self):
        active_slot = LayoutSlot.objects.get(code="home_hero")
        inactive_slot = LayoutSlot.objects.create(
            title="Inactive home slot",
            code="inactive_home_filter_slot",
            scope=LayoutSlot.Scope.HOME,
            max_items=1,
            is_active=False,
            sort_order=999,
        )

        for value, expected_state, expected_slot in (
            ("1", True, active_slot),
            ("0", False, inactive_slot),
        ):
            with self.subTest(active=value):
                response = self.client.get(
                    reverse("layout-slots:index"),
                    data={"scope": LayoutSlot.Scope.HOME, "active": value},
                )

                self.assertEqual(response.status_code, 200)
                scope_slots = list(response.context["scope_slots"])
                self.assertTrue(scope_slots)
                self.assertTrue(
                    all(slot.is_active is expected_state for slot in scope_slots)
                )
                self.assertIn(expected_slot, scope_slots)
                self.assertEqual(
                    response.context["selected_slot"].is_active,
                    expected_state,
                )

    def test_controlled_configuration_updates_slot_and_records_audit_log(self):
        slot = LayoutSlot.objects.get(code="home_featured")
        response = self.client.post(
            reverse("layout-slots:index"),
            data=self.slot_payload(
                slot,
                title="Homepage curated features",
                max_items=4,
                fill_mode=LayoutSlot.FillMode.AUTO,
                description="Use approved placements in the fixed homepage grid.",
                sort_order=25,
            ),
        )

        self.assertEqual(response.status_code, 302)
        slot.refresh_from_db()
        self.assertEqual(slot.title, "Homepage curated features")
        self.assertEqual(slot.max_items, 4)
        self.assertEqual(slot.fill_mode, LayoutSlot.FillMode.AUTO)
        self.assertEqual(slot.sort_order, 25)
        audit = AuditLog.objects.get(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=self.superuser,
            target_type="LayoutSlot",
            target_id=str(slot.pk),
        )
        self.assertEqual(audit.metadata["before"]["code"], "home_featured")
        self.assertEqual(audit.metadata["after"]["max_items"], 4)
        self.assertEqual(audit.metadata["after"]["fill_mode"], "auto")

    def test_cannot_lower_max_items_below_existing_active_target_count(self):
        slot = LayoutSlot.objects.get(code="home_featured")
        ArticlePlacement.objects.create(slot=slot, article=self.article_a)
        ArticlePlacement.objects.create(slot=slot, article=self.article_b)

        response = self.client.post(
            reverse("layout-slots:index"),
            data=self.slot_payload(slot, max_items=1),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前最多有 2 条启用投放")
        slot.refresh_from_db()
        self.assertNotEqual(slot.max_items, 1)
        self.assertFalse(AuditLog.objects.filter(target_id=str(slot.pk)).exists())

    def test_current_content_and_preview_reuse_formal_query_for_all_scopes(self):
        cases = (
            (
                LayoutSlot.objects.get(code="home_featured"),
                make_target_value(ArticlePlacement.TargetType.MAIN_SITE),
                ArticlePlacement.TargetType.MAIN_SITE,
                "",
            ),
            (
                LayoutSlot.objects.get(code="section_article_list"),
                make_target_value(ArticlePlacement.TargetType.SECTION, "news"),
                ArticlePlacement.TargetType.SECTION,
                "news",
            ),
            (
                LayoutSlot.objects.get(code="journal_featured"),
                make_target_value(
                    ArticlePlacement.TargetType.JOURNAL, self.journal.slug
                ),
                ArticlePlacement.TargetType.JOURNAL,
                self.journal.slug,
            ),
        )
        for index, (slot, target_value, target_type, target_slug) in enumerate(cases):
            placement = ArticlePlacement.objects.create(
                slot=slot,
                article=self.article_a,
                target_type=target_type,
                target_slug=target_slug,
                sort_order=index,
            )
            with self.subTest(scope=slot.scope):
                response = self.client.get(
                    reverse("layout-slots:index"),
                    data={
                        "scope": slot.scope,
                        "slot": slot.pk,
                        "target": target_value,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response, f'data-slot-placement-id="{placement.pk}"'
                )
                self.assertContains(
                    response,
                    f'data-placement-article="{self.article_a.static_slug}"',
                )
                self.assertContains(response, "当前上线内容")
                self.assertContains(response, "版位预览")

    def test_future_placement_is_excluded_from_online_content_and_preview(self):
        slot = LayoutSlot.objects.get(code="section_article_list")
        placement = ArticlePlacement.objects.create(
            slot=slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
            starts_at=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(
            reverse("layout-slots:index"),
            data={
                "scope": LayoutSlot.Scope.SECTION,
                "slot": slot.pk,
                "target": make_target_value(
                    ArticlePlacement.TargetType.SECTION, "news"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'data-slot-placement-id="{placement.pk}"')
        self.assertContains(response, "当前上线内容：新闻（0）")
        self.assertContains(response, "暂无可预览的上线内容")

    def test_module_access_is_separate_from_change_permission(self):
        user = get_user_model().objects.create_user(
            "slot-viewer",
            email="slot-viewer@example.com",
            display_name="Slot Viewer",
            password="password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin", codename="access_admin"
            ),
            Permission.objects.get(
                content_type__app_label="site_settings", codename="access_slots"
            ),
        )
        self.client.force_login(user)
        slot = LayoutSlot.objects.get(code="home_hero")

        response = self.client.get(reverse("layout-slots:index"))
        self.assertIn(response.status_code, (302, 403))
        response = self.client.post(
            reverse("layout-slots:index"), data=self.slot_payload(slot, max_items=2)
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")
        slot.refresh_from_db()
        self.assertEqual(slot.max_items, 1)

    def test_standard_roles_receive_slot_module_access(self):
        self.assertEqual(set(ROLE_DEFINITIONS), {"super_admin"})
        self.assertEqual(ROLE_DEFINITIONS["super_admin"]["custom_permissions"], "*")
