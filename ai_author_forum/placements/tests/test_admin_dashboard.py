from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.models import StaticPublishJob

from ..forms import make_target_value
from ..models import ArticlePlacement, LayoutSlot


class PlacementDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = HomePage.objects.first()
        cls.news_listing = NewsListingPage(
            title="Placement articles",
            slug="placement-articles",
            introduction="Placement test articles.",
        )
        cls.home.add_child(instance=cls.news_listing)
        cls.news_listing.save_revision().publish()
        cls.journal = Journal.objects.create(
            name="Nature AI",
            name_cn="自然 AI",
            slug="nature-ai-dashboard",
            az_group="N",
            status="active",
        )
        cls.other_journal = Journal.objects.create(
            name="Other Journal",
            slug="other-journal-dashboard",
            az_group="O",
            status="active",
        )
        cls.approved_article = cls.create_article(
            "Approved placement article",
            "approved-placement-article",
            ArticlePage.ReviewStatus.APPROVED,
        )
        cls.draft_article = cls.create_article(
            "Draft placement article",
            "draft-placement-article",
            ArticlePage.ReviewStatus.DRAFT,
        )
        cls.approved_unpublished_article = cls.create_article(
            "Approved review-only article",
            "approved-review-only-article",
            ArticlePage.ReviewStatus.APPROVED,
        )
        ArticlePage.objects.filter(pk=cls.approved_unpublished_article.pk).update(
            live=False
        )
        cls.approved_unpublished_article.refresh_from_db()
        cls.superuser = get_user_model().objects.create_superuser(
            "placement-admin", "placement@example.com", "password"
        )

    @classmethod
    def create_article(cls, title, slug, review_status, journal=None):
        article = ArticlePage(
            title=title,
            slug=slug,
            abstract=f"{title} abstract.",
            body=[{"type": "paragraph", "value": f"{title} body."}],
            authors="Editor",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            review_status=review_status,
            primary_journal=journal or cls.journal,
            keywords="ai",
            static_slug=slug,
        )
        cls.news_listing.add_child(instance=article)
        article.save_revision().publish()
        return article

    def setUp(self):
        self.client.force_login(self.superuser)

    def placement_payload(self, **overrides):
        payload = {
            "article": self.approved_article.pk,
            "target": make_target_value(ArticlePlacement.TargetType.MAIN_SITE),
            "slot": LayoutSlot.objects.get(code="home_featured").pk,
            "override_title": "Curated title",
            "override_summary": "Curated summary",
            "is_pinned": "on",
            "sort_order": 7,
            "starts_at": "",
            "ends_at": "",
            "is_active": "on",
            "action": "save",
        }
        payload.update(overrides)
        return payload

    def test_dashboard_filters_article_choices_to_approved_articles(self):
        response = self.client.get(
            reverse("placements:index"),
            data={"article_query": "placement article"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.approved_article.title)
        self.assertNotContains(response, self.draft_article.title)
        self.assertContains(response, "共有 1 篇可投放文章")

    def test_dashboard_includes_approved_article_not_published_in_wagtail(self):
        self.assertFalse(self.approved_unpublished_article.live)

        response = self.client.get(
            reverse("placements:index"),
            data={"article_query": "review-only"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.approved_unpublished_article.title)

    def test_dashboard_exposes_all_controlled_targets_and_slots(self):
        response = self.client.get(reverse("placements:index"))

        self.assertContains(response, 'value="main_site:"')
        self.assertContains(response, 'value="section:news"')
        self.assertContains(response, f'value="journal:{self.journal.slug}"')
        self.assertContains(response, 'value="search:search"')
        self.assertContains(response, "section_article_list")
        self.assertContains(response, "search_recommended")

    def test_dashboard_renders_placement_image_alt_control_and_guidance(self):
        response = self.client.get(reverse("placements:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="override_image_alt"', html=False)

        english_template = Path(
            "ai_author_forum/placements/templates/placements/admin/dashboard.en.html"
        ).read_text(encoding="utf-8")
        self.assertIn("form.override_image_alt", english_template)
        self.assertIn(
            "formal publication still requires usable Alt text",
            english_template,
        )

    def test_bulk_dashboard_bootstraps_article_refresh(self):
        response = self.client.get(reverse("placements:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-bulk-articles-message")
        self.assertContains(
            response,
            "/static/placements/placements-workbench.js?v=20260730-2",
        )

        script_path = finders.find("placements/placements-workbench.js")
        self.assertIsNotNone(script_path)
        script = Path(script_path).read_text(encoding="utf-8")
        self.assertIn(
            "journal?.addEventListener('change', refreshJournalSelection)", script
        )
        self.assertIn("    loadJournalArticles();\n    updateBulkCapacity();", script)
        self.assertIn("cache: 'no-store'", script)

    def test_preview_validates_and_renders_without_saving(self):
        response = self.client.post(
            reverse("placements:index"),
            data=self.placement_payload(action="preview"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-placement-preview="true"')
        self.assertContains(response, "Curated title")
        self.assertContains(response, "Curated summary")
        self.assertEqual(ArticlePlacement.objects.count(), 0)
        self.assertEqual(
            AuditLog.objects.filter(target_type="ArticlePlacement").count(),
            0,
        )

    def test_save_persists_full_placement_configuration_and_audit(self):
        starts_at = timezone.localtime().replace(second=0, microsecond=0)
        ends_at = starts_at + timedelta(days=7)
        response = self.client.post(
            reverse("placements:index"),
            data=self.placement_payload(
                starts_at=starts_at.strftime("%Y-%m-%dT%H:%M"),
                ends_at=ends_at.strftime("%Y-%m-%dT%H:%M"),
            ),
        )

        placement = ArticlePlacement.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(placement.target_type, ArticlePlacement.TargetType.MAIN_SITE)
        self.assertEqual(placement.target_slug, "")
        self.assertEqual(placement.override_title, "Curated title")
        self.assertEqual(placement.override_summary, "Curated summary")
        self.assertTrue(placement.is_pinned)
        self.assertEqual(placement.sort_order, 7)
        self.assertEqual(placement.starts_at, starts_at)
        self.assertEqual(placement.ends_at, ends_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.SUCCESS,
                actor=self.superuser,
                target_id=str(placement.pk),
            ).exists()
        )

    def test_all_required_target_types_can_be_saved(self):
        cases = (
            (
                "main_site",
                make_target_value(ArticlePlacement.TargetType.MAIN_SITE),
                "home_hero",
            ),
            (
                "section",
                make_target_value(ArticlePlacement.TargetType.SECTION, "news"),
                "section_top_story",
            ),
            (
                "journal",
                make_target_value(
                    ArticlePlacement.TargetType.JOURNAL, self.journal.slug
                ),
                "journal_hero",
            ),
            (
                "search",
                make_target_value(ArticlePlacement.TargetType.SEARCH, "search"),
                "search_recommended",
            ),
        )
        for index, (expected_type, target, slot_code) in enumerate(cases, start=1):
            with self.subTest(target=expected_type):
                response = self.client.post(
                    reverse("placements:index"),
                    data=self.placement_payload(
                        target=target,
                        slot=LayoutSlot.objects.get(code=slot_code).pk,
                        sort_order=index,
                    ),
                )
                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    ArticlePlacement.objects.filter(
                        target_type=expected_type,
                        slot__code=slot_code,
                    ).exists()
                )

    def test_target_scope_mismatch_is_rejected(self):
        response = self.client.post(
            reverse("placements:index"),
            data=self.placement_payload(
                target=make_target_value(ArticlePlacement.TargetType.SECTION, "news"),
                slot=LayoutSlot.objects.get(code="home_featured").pk,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "所选目标只能使用 Section 版位")
        self.assertFalse(ArticlePlacement.objects.exists())

    def test_cross_journal_placement_is_rejected(self):
        response = self.client.post(
            reverse("placements:index"),
            data=self.placement_payload(
                target=make_target_value(
                    ArticlePlacement.TargetType.JOURNAL, self.other_journal.slug
                ),
                slot=LayoutSlot.objects.get(code="journal_featured").pk,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "文章不属于所选子期刊")
        self.assertFalse(ArticlePlacement.objects.exists())

    def test_current_effective_content_reuses_schedule_and_target_rules(self):
        slot = LayoutSlot.objects.get(code="section_article_list")
        active = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=slot,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
            override_title="Currently effective",
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="section_sidebar"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
            starts_at=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(
            reverse("placements:index"),
            data={
                "target": make_target_value(
                    ArticlePlacement.TargetType.SECTION, "news"
                ),
                "slot": slot.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-placement-id="{active.pk}"')
        self.assertContains(response, "当前生效内容（1）")

    def test_dashboard_active_filter_returns_only_requested_state(self):
        active = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_featured"),
            override_title="Active dashboard placement",
            is_active=True,
        )
        inactive = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_hero"),
            override_title="Inactive dashboard placement",
            is_active=False,
        )

        for value, expected in (("1", active), ("0", inactive)):
            with self.subTest(active=value):
                response = self.client.get(
                    reverse("placements:index"), data={"active": value}
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item.pk for item in response.context["current_placements"]],
                    [expected.pk],
                )

    def test_dashboard_expired_filter_combines_with_active_filter(self):
        now = timezone.now()
        expired_active = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_featured"),
            override_title="Expired active placement",
            ends_at=now - timedelta(hours=1),
            is_active=True,
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_hero"),
            override_title="Future active placement",
            ends_at=now + timedelta(days=30),
            is_active=True,
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="section_sidebar"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
            override_title="Expired inactive placement",
            ends_at=now - timedelta(hours=2),
            is_active=False,
        )

        response = self.client.get(
            reverse("placements:index"), data={"expired": "1", "active": "1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.pk for item in response.context["current_placements"]],
            [expired_active.pk],
        )

    def test_dashboard_expires_within_filter_returns_only_upcoming_active_items(self):
        now = timezone.now()
        upcoming = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_featured"),
            override_title="Upcoming placement",
            ends_at=now + timedelta(days=3),
            is_active=True,
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_hero"),
            override_title="Later placement",
            ends_at=now + timedelta(days=14),
            is_active=True,
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="section_article_list"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
            override_title="Already expired placement",
            ends_at=now - timedelta(hours=1),
            is_active=True,
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="section_sidebar"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
            override_title="Inactive upcoming placement",
            ends_at=now + timedelta(days=2),
            is_active=False,
        )

        response = self.client.get(
            reverse("placements:index"), data={"expires_within": "7"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.pk for item in response.context["current_placements"]],
            [upcoming.pk],
        )

    def test_dashboard_capacity_filter_returns_only_active_over_capacity_group(self):
        over_capacity_slot = LayoutSlot.objects.get(code="home_featured")
        over_capacity_slot.max_items = 1
        over_capacity_slot.save(update_fields=["max_items"])
        extra_article = self.create_article(
            "Capacity dashboard article", "capacity-dashboard-article", "approved"
        )
        inactive_article = self.create_article(
            "Inactive capacity article", "inactive-capacity-article", "approved"
        )
        over_capacity = {
            ArticlePlacement.objects.create(
                article=self.approved_article,
                slot=over_capacity_slot,
                override_title="Over capacity one",
                is_active=True,
            ).pk,
            ArticlePlacement.objects.create(
                article=extra_article,
                slot=over_capacity_slot,
                override_title="Over capacity two",
                is_active=True,
            ).pk,
        }
        inactive = ArticlePlacement.objects.create(
            article=inactive_article,
            slot=over_capacity_slot,
            override_title="Inactive in over capacity group",
            is_active=False,
        )
        normal = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_hero"),
            override_title="Normal capacity placement",
            is_active=True,
        )

        response = self.client.get(
            reverse("placements:index"), data={"capacity": "over"}
        )

        self.assertEqual(response.status_code, 200)
        result_ids = {item.pk for item in response.context["current_placements"]}
        self.assertEqual(result_ids, over_capacity)
        self.assertNotIn(inactive.pk, result_ids)
        self.assertNotIn(normal.pk, result_ids)

    def test_edit_updates_existing_placement(self):
        placement = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_featured"),
        )
        response = self.client.post(
            reverse("placements:index"),
            data=self.placement_payload(
                placement_id=placement.pk,
                override_title="Updated placement title",
                is_pinned="",
                sort_order=22,
            ),
        )

        self.assertEqual(response.status_code, 302)
        placement.refresh_from_db()
        self.assertEqual(placement.override_title, "Updated placement title")
        self.assertFalse(placement.is_pinned)
        self.assertEqual(placement.sort_order, 22)
        self.assertEqual(ArticlePlacement.objects.count(), 1)

    def test_module_access_does_not_grant_write_permission(self):
        user = get_user_model().objects.create_user(
            "placement-viewer", password="password", is_staff=True
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin", codename="access_admin"
            ),
            Permission.objects.get(
                content_type__app_label="site_settings",
                codename="access_placements",
            ),
        )
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("placements:index")).status_code, 200)
        response = self.client.post(
            reverse("placements:index"), data=self.placement_payload()
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")
        self.assertFalse(ArticlePlacement.objects.exists())

    def test_bulk_journal_placement_creates_selected_articles_and_one_audit(self):
        second_article = self.create_article(
            "Second batch article",
            "second-batch-article",
            ArticlePage.ReviewStatus.APPROVED,
        )
        slot = LayoutSlot.objects.get(code="journal_featured")

        response = self.client.post(
            reverse("placements:index"),
            data={
                "mode": "bulk_journal",
                "journal": self.journal.slug,
                "articles": [self.approved_article.pk, second_article.pk],
                "slot": slot.pk,
                "starts_at": "",
                "ends_at": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        placements = ArticlePlacement.objects.filter(
            slot=slot,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
        ).order_by("sort_order")
        self.assertEqual(
            list(placements.values_list("article_id", flat=True)),
            [self.approved_article.pk, second_article.pk],
        )
        audit = AuditLog.objects.get(message="批量投放文章到同一子期刊。")
        self.assertEqual(audit.action, AuditAction.CONFIGURE)
        self.assertEqual(audit.status, AuditStatus.SUCCESS)
        self.assertEqual(
            audit.metadata["created_article_ids"],
            [self.approved_article.pk, second_article.pk],
        )

    @patch(
        "ai_author_forum.static_publish.tasks.run_coalesced_static_publish.apply_async",
        side_effect=RuntimeError(
            "Retry limit exceeded while trying to reconnect to the Celery result store backend."
        ),
    )
    def test_bulk_journal_placement_survives_publish_queue_failure(self, _enqueue):
        slot = LayoutSlot.objects.get(code="journal_featured")

        with self.assertLogs("django.test", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("placements:index"),
                    data={
                        "mode": "bulk_journal",
                        "journal": self.journal.slug,
                        "articles": [self.approved_article.pk],
                        "slot": slot.pk,
                        "starts_at": "",
                        "ends_at": "",
                    },
                )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ArticlePlacement.objects.filter(
                article=self.approved_article,
                slot=slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=self.journal.slug,
            ).exists()
        )
        job = StaticPublishJob.objects.get(is_automatic=True)
        self.assertEqual(job.status, StaticPublishJob.Status.FAILED)
        self.assertIn("Celery result store backend", job.error)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(job.pk),
                metadata__stage="queue",
            ).exists()
        )

    def test_bulk_journal_placement_skips_existing_active_placement(self):
        second_article = self.create_article(
            "Batch skip article",
            "batch-skip-article",
            ArticlePage.ReviewStatus.APPROVED,
        )
        slot = LayoutSlot.objects.get(code="journal_featured")
        existing = ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=slot,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
        )

        response = self.client.post(
            reverse("placements:index"),
            data={
                "mode": "bulk_journal",
                "journal": self.journal.slug,
                "articles": [self.approved_article.pk, second_article.pk],
                "slot": slot.pk,
                "starts_at": "",
                "ends_at": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "跳过已投放 1 篇")
        self.assertEqual(
            ArticlePlacement.objects.filter(
                slot=slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=self.journal.slug,
            ).count(),
            2,
        )
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)

    def test_bulk_journal_placement_rejects_cross_journal_article(self):
        other_article = self.create_article(
            "Other journal batch article",
            "other-journal-batch-article",
            ArticlePage.ReviewStatus.APPROVED,
            journal=self.other_journal,
        )
        slot = LayoutSlot.objects.get(code="journal_featured")

        response = self.client.post(
            reverse("placements:index"),
            data={
                "mode": "bulk_journal",
                "journal": self.journal.slug,
                "articles": [other_article.pk],
                "slot": slot.pk,
                "starts_at": "",
                "ends_at": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("articles", response.context["bulk_form"].errors)
        self.assertFalse(
            ArticlePlacement.objects.filter(
                article=other_article,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=self.journal.slug,
            ).exists()
        )

    def test_bulk_journal_placement_is_atomic_when_capacity_is_insufficient(self):
        second_article = self.create_article(
            "Batch capacity article",
            "batch-capacity-article",
            ArticlePage.ReviewStatus.APPROVED,
        )
        slot = LayoutSlot.objects.get(code="journal_featured")
        slot.max_items = 1
        slot.save(update_fields=["max_items"])

        response = self.client.post(
            reverse("placements:index"),
            data={
                "mode": "bulk_journal",
                "journal": self.journal.slug,
                "articles": [self.approved_article.pk, second_article.pk],
                "slot": slot.pk,
                "starts_at": "",
                "ends_at": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前仅剩 1 个可用名额")
        self.assertFalse(
            ArticlePlacement.objects.filter(
                slot=slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=self.journal.slug,
            ).exists()
        )

    def test_bulk_article_endpoint_returns_only_target_journal_articles(self):
        other_article = self.create_article(
            "Other endpoint article",
            "other-endpoint-article",
            ArticlePage.ReviewStatus.APPROVED,
            journal=self.other_journal,
        )

        response = self.client.get(
            reverse("placements:bulk_articles"),
            data={"journal": self.journal.slug},
        )

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.json()["articles"]}
        self.assertIn(self.approved_article.pk, returned_ids)
        self.assertNotIn(other_article.pk, returned_ids)

    def test_homepage_composition_requires_placements_module_access(self):
        user = get_user_model().objects.create_user(
            "homepage-no-access", password="test", is_staff=True
        )
        user.user_permissions.add(Permission.objects.get(codename="access_admin"))
        self.client.force_login(user)

        response = self.client.get(reverse("homepage-composition:index"))

        self.assertIn(response.status_code, (302, 403))

    def test_homepage_composition_uses_fixed_documented_panel_order(self):
        response = self.client.get(reverse("homepage-composition:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [panel["code"] for panel in response.context["panels"]],
            ["home_hero", "home_visual_stories", "home_featured"],
        )
        self.assertContains(response, 'class="home-composition__intro"', html=False)
        self.assertNotContains(response, 'draggable="true"', html=False)

    def test_homepage_composition_reports_count_alt_schedule_and_duplicate_warnings(
        self,
    ):
        now = timezone.now()
        hero_slot = LayoutSlot.objects.get(code="home_hero")
        visual_slot = LayoutSlot.objects.get(code="home_visual_stories")
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=hero_slot,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=3),
        )
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=visual_slot,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=4),
        )
        ArticlePlacement.objects.create(
            article=self.approved_unpublished_article,
            slot=visual_slot,
            ends_at=now - timedelta(minutes=1),
        )

        response = self.client.get(reverse("homepage-composition:index"))

        self.assertContains(
            response, "Formal publication requires exactly 1 effective item(s)"
        )
        self.assertContains(
            response, "Formal publication requires exactly 2 effective item(s)"
        )
        self.assertContains(
            response, "A valid image Alt is required for formal publication"
        )
        self.assertContains(
            response, "This article overlaps another principal homepage slot"
        )
        self.assertContains(response, "Scheduled")
        self.assertContains(response, "Expired")

    def test_homepage_composition_preview_reuses_formal_hero_and_visual_components(
        self,
    ):
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_hero"),
            override_image_alt="Hero preview alt",
        )
        ArticlePlacement.objects.create(
            article=self.approved_unpublished_article,
            slot=LayoutSlot.objects.get(code="home_visual_stories"),
            override_image_alt="Visual preview alt",
        )

        response = self.client.get(reverse("homepage-composition:index"))

        self.assertContains(
            response, 'class="c-static-article-hero"', count=2, html=False
        )
        self.assertContains(
            response, 'class="c-visual-story-card"', count=2, html=False
        )
        self.assertContains(
            response,
            'class="home-preview__frame home-preview__frame--mobile"',
            count=3,
            html=False,
        )

    def test_homepage_composition_shows_pending_publisher_job(self):
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.PENDING,
            scope=StaticPublishJob.Scope.SELECTIVE,
            requested_paths=["/"],
            is_automatic=False,
            triggered_by=self.superuser,
            summary={"requires_publisher_approval": True},
        )

        response = self.client.get(reverse("homepage-composition:index"))

        self.assertContains(
            response, f'data-pending-publish-job="{job.pk}"', html=False
        )

    def test_homepage_composition_readonly_user_has_no_edit_or_add_actions(self):
        ArticlePlacement.objects.create(
            article=self.approved_article,
            slot=LayoutSlot.objects.get(code="home_hero"),
        )
        user = get_user_model().objects.create_user(
            "homepage-readonly", password="test", is_staff=True
        )
        user.user_permissions.add(
            Permission.objects.get(codename="access_admin"),
            Permission.objects.get(
                content_type__app_label="site_settings",
                codename="access_placements",
            ),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("homepage-composition:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "?edit=")
        self.assertNotContains(
            response,
            reverse("wagtailadmin_pages:edit", args=[self.approved_article.pk]),
        )
        self.assertNotContains(
            response, f"slot={LayoutSlot.objects.get(code='home_hero').pk}"
        )
