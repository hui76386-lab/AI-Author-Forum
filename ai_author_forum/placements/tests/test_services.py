from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from ai_author_forum.articles.display import resolve_article_image
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.placements.services import (
    get_home_page_placement_context,
    get_slot_items,
)


class PlacementServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = HomePage.objects.first()
        cls.news_listing = NewsListingPage(
            title="News",
            slug="news",
            introduction="Latest updates.",
        )
        cls.home.add_child(instance=cls.news_listing)
        cls.news_listing.save_revision().publish()

        cls.journal = Journal.objects.create(
            name="Nature AI",
            name_cn="Nature AI",
            slug="nature-ai",
            az_group="N",
            status="active",
        )
        cls.slot = LayoutSlot.objects.get(code="home_featured")
        cls.slot.max_items = 3
        cls.slot.save()

        cls.article_a = cls.create_article("Article A", "article-a")
        cls.article_b = cls.create_article("Article B", "article-b")
        cls.article_c = cls.create_article("Article C", "article-c")

    @classmethod
    def create_article(cls, title, slug):
        article = ArticlePage(
            title=title,
            slug=slug,
            abstract=f"{title} abstract.",
            body=[{"type": "paragraph", "value": f"{title} body."}],
            authors="Editor",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            review_status=ArticlePage.ReviewStatus.APPROVED,
            primary_journal=cls.journal,
            keywords="ai",
            static_slug=slug,
        )
        cls.news_listing.add_child(instance=article)
        article.save_revision().publish()
        return article

    def test_get_slot_items_returns_active_live_items_in_display_order(self):
        last_normal = ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_a,
            sort_order=20,
        )
        pinned = ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_b,
            is_pinned=True,
            sort_order=99,
        )
        first_normal = ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_c,
            sort_order=10,
        )

        placements = list(get_slot_items("home_featured"))

        self.assertEqual(placements, [pinned, first_normal, last_normal])

    def test_get_slot_items_filters_target_and_schedule(self):
        now = timezone.now()
        ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug="nature-ai",
        )
        ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_b,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug="other-journal",
        )
        ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_c,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug="nature-ai",
            starts_at=now + timedelta(days=1),
        )

        placements = list(
            get_slot_items(
                "home_featured",
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug="nature-ai",
            )
        )

        self.assertEqual(
            [placement.article for placement in placements], [self.article_a]
        )

    def test_auto_fill_mode_backfills_from_already_available_articles(self):
        section_slot = LayoutSlot.objects.get(code="section_article_list")
        ArticlePlacement.objects.create(
            slot=section_slot,
            article=self.article_b,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
        )
        manual = ArticlePlacement.objects.create(
            slot=self.slot,
            article=self.article_a,
            sort_order=10,
        )
        self.slot.fill_mode = LayoutSlot.FillMode.AUTO
        self.slot.max_items = 2
        self.slot.save(update_fields=("fill_mode", "max_items"))

        placements = list(get_slot_items("home_featured"))

        self.assertEqual(
            [placement.article for placement in placements],
            [self.article_a, self.article_b],
        )
        self.assertEqual(placements[0], manual)
        self.assertFalse(getattr(placements[0], "is_auto_filled", False))
        self.assertTrue(placements[1].is_auto_filled)
        self.assertIsNone(placements[1].pk)
        self.assertNotIn(
            self.article_c, [placement.article for placement in placements]
        )

    def test_category_slot_manual_placement_overrides_system_before_limit(self):
        category = JournalCategory.objects.create(
            journal=self.journal,
            name="Machine learning",
            code="ML",
            slug="machine-learning",
        )
        category_slot = LayoutSlot.objects.create(
            title="Category listing",
            code="test-category-listing",
            scope=LayoutSlot.Scope.CATEGORY,
            max_items=3,
        )
        manual = ArticlePlacement.objects.create(
            slot=category_slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category=category,
            placement_kind=ArticlePlacement.PlacementKind.FEATURED,
            source=ArticlePlacement.Source.MANUAL,
            is_pinned=True,
            sort_order=0,
        )
        ArticlePlacement.objects.create(
            slot=category_slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category=category,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
            source=ArticlePlacement.Source.SYSTEM,
            is_pinned=True,
            sort_order=1,
        )
        second = ArticlePlacement.objects.create(
            slot=category_slot,
            article=self.article_b,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category=category,
            placement_kind=ArticlePlacement.PlacementKind.FEATURED,
            source=ArticlePlacement.Source.MANUAL,
            sort_order=2,
        )

        placements = get_slot_items(
            category_slot.code,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category=category,
            limit=2,
        )

        self.assertEqual(list(placements), [manual, second])
        self.assertEqual(
            [placement.article_id for placement in placements],
            [self.article_a.pk, self.article_b.pk],
        )

    def test_category_slot_requires_explicit_category_target(self):
        with self.assertRaisesRegex(ValueError, "target_category is required"):
            get_slot_items(
                "category_default_listing",
                target_type=ArticlePlacement.TargetType.CATEGORY,
            )

    def test_home_page_context_auto_fill_excludes_previous_slots(self):
        hero_slot = LayoutSlot.objects.get(code="home_hero")
        latest_slot = LayoutSlot.objects.get(code="latest_ai_article")
        section_slot = LayoutSlot.objects.get(code="section_article_list")
        ArticlePlacement.objects.create(slot=hero_slot, article=self.article_a)
        ArticlePlacement.objects.create(
            slot=section_slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="ai-article",
        )
        ArticlePlacement.objects.create(
            slot=section_slot,
            article=self.article_b,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="ai-article",
        )
        self.slot.fill_mode = LayoutSlot.FillMode.AUTO
        self.slot.max_items = 2
        self.slot.save(update_fields=("fill_mode", "max_items"))
        latest_slot.fill_mode = LayoutSlot.FillMode.AUTO
        latest_slot.max_items = 2
        latest_slot.save(update_fields=("fill_mode", "max_items"))

        context = get_home_page_placement_context()
        featured_articles = [
            placement.article for placement in context["featured_placements"]
        ]
        latest_articles = [
            placement.article for placement in context["latest_ai_article_placements"]
        ]

        self.assertEqual(
            [placement.article for placement in context["hero_placements"]],
            [self.article_a],
        )
        self.assertNotIn(self.article_a, featured_articles)
        self.assertIn(self.article_b, featured_articles)
        self.assertNotIn(self.article_c, featured_articles + latest_articles)
        self.assertLessEqual(len(featured_articles), self.slot.max_items)

    def test_clean_rejects_capacity_overflow_for_same_target(self):
        limited_slot = LayoutSlot.objects.create(
            title="Hero",
            code="test_home_hero",
            scope=LayoutSlot.Scope.HOME,
            max_items=1,
        )
        ArticlePlacement.objects.create(
            slot=limited_slot,
            article=self.article_a,
        )
        placement = ArticlePlacement(
            slot=limited_slot,
            article=self.article_b,
        )

        with self.assertRaises(ValidationError):
            placement.full_clean()

    def test_approved_non_live_article_can_be_placed_and_rendered(self):
        ArticlePage.objects.filter(pk=self.article_a.pk).update(live=False)
        self.article_a.refresh_from_db()
        placement = ArticlePlacement(
            slot=self.slot,
            article=self.article_a,
        )

        placement.full_clean()
        placement.save()

        self.assertFalse(self.article_a.live)
        self.assertEqual(list(get_slot_items(self.slot.code)), [placement])

    def test_clean_rejects_unapproved_article(self):
        self.article_a.review_status = ArticlePage.ReviewStatus.DRAFT
        self.article_a.save(update_fields=("review_status",))
        placement = ArticlePlacement(
            slot=self.slot,
            article=self.article_a,
        )

        with self.assertRaises(ValidationError):
            placement.full_clean()

    def test_clean_rejects_slot_scope_mismatch(self):
        placement = ArticlePlacement(
            slot=self.slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
        )

        with self.assertRaises(ValidationError):
            placement.full_clean()

    def test_clean_rejects_cross_journal_target(self):
        other_journal = Journal.objects.create(
            name="Other Journal",
            slug="other-journal-placement",
            az_group="O",
            status="active",
        )
        journal_slot = LayoutSlot.objects.get(code="journal_featured")
        placement = ArticlePlacement(
            slot=journal_slot,
            article=self.article_a,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=other_journal.slug,
        )

        with self.assertRaises(ValidationError):
            placement.full_clean()

    def test_home_hero_allows_only_one_overlapping_effective_placement(self):
        hero_slot = LayoutSlot.objects.get(code="home_hero")
        ArticlePlacement.objects.create(slot=hero_slot, article=self.article_a)

        candidate = ArticlePlacement(slot=hero_slot, article=self.article_b)

        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIn("slot", raised.exception.message_dict)

    def test_home_visual_stories_allows_only_two_overlapping_effective_placements(self):
        visual_slot = LayoutSlot.objects.get(code="home_visual_stories")
        ArticlePlacement.objects.create(slot=visual_slot, article=self.article_a)
        ArticlePlacement.objects.create(slot=visual_slot, article=self.article_b)

        candidate = ArticlePlacement(slot=visual_slot, article=self.article_c)

        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIn("slot", raised.exception.message_dict)

    def test_cross_home_slot_duplicate_is_rejected_for_overlapping_windows(self):
        now = timezone.now()
        hero_slot = LayoutSlot.objects.get(code="home_hero")
        visual_slot = LayoutSlot.objects.get(code="home_visual_stories")
        ArticlePlacement.objects.create(
            slot=hero_slot,
            article=self.article_a,
            starts_at=now,
            ends_at=now + timedelta(hours=2),
        )
        candidate = ArticlePlacement(
            slot=visual_slot,
            article=self.article_a,
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=3),
        )

        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIn("article", raised.exception.message_dict)

    def test_cross_home_slot_duplicate_allows_adjacent_half_open_windows(self):
        now = timezone.now()
        boundary = now + timedelta(hours=1)
        hero_slot = LayoutSlot.objects.get(code="home_hero")
        visual_slot = LayoutSlot.objects.get(code="home_visual_stories")
        ArticlePlacement.objects.create(
            slot=hero_slot,
            article=self.article_a,
            starts_at=now,
            ends_at=boundary,
        )
        candidate = ArticlePlacement(
            slot=visual_slot,
            article=self.article_a,
            starts_at=boundary,
            ends_at=boundary + timedelta(hours=1),
        )

        candidate.full_clean()

    def test_homepage_context_prioritises_hero_then_visual_then_featured(self):
        hero_slot = LayoutSlot.objects.get(code="home_hero")
        visual_slot = LayoutSlot.objects.get(code="home_visual_stories")
        ArticlePlacement.objects.create(slot=hero_slot, article=self.article_a)
        ArticlePlacement.objects.create(slot=visual_slot, article=self.article_a)
        visual_b = ArticlePlacement.objects.create(
            slot=visual_slot, article=self.article_b
        )
        ArticlePlacement.objects.create(slot=self.slot, article=self.article_a)
        featured_c = ArticlePlacement.objects.create(
            slot=self.slot, article=self.article_c
        )

        context = get_home_page_placement_context()

        self.assertEqual(
            [placement.article_id for placement in context["hero_placements"]],
            [self.article_a.pk],
        )
        self.assertEqual(context["visual_story_placements"], [visual_b])
        self.assertEqual(context["featured_placements"], [featured_c])

    def test_article_image_resolution_uses_documented_image_priority(self):
        placement_image = SimpleNamespace(title="Placement image")
        article_image = SimpleNamespace(title="Article image")
        site_image = SimpleNamespace(title="Site image")
        article = SimpleNamespace(
            featured_image=article_image,
            featured_image_alt="Article image alt",
            title="Article title",
        )
        placement = SimpleNamespace(
            override_image=placement_image,
            override_image_alt="Placement image alt",
        )

        resolved = resolve_article_image(
            article,
            placement=placement,
            site_settings=SimpleNamespace(default_image=site_image),
        )
        self.assertIs(resolved.image, placement_image)
        self.assertEqual(resolved.source, "placement")

        placement.override_image = None
        resolved = resolve_article_image(
            article,
            placement=placement,
            site_settings=SimpleNamespace(default_image=site_image),
        )
        self.assertIs(resolved.image, article_image)
        self.assertEqual(resolved.source, "article")

        article.featured_image = None
        resolved = resolve_article_image(
            article,
            placement=placement,
            site_settings=SimpleNamespace(default_image=site_image),
        )
        self.assertIs(resolved.image, site_image)
        self.assertEqual(resolved.source, "site")

    def test_article_image_alt_uses_placement_then_article_then_image_title(self):
        image = SimpleNamespace(title="Image title")
        article = SimpleNamespace(
            featured_image=image,
            featured_image_alt="Article image alt",
            title="Article title",
        )
        placement = SimpleNamespace(
            override_image=None,
            override_image_alt="Placement image alt",
        )
        site_settings = SimpleNamespace(default_image=None)

        self.assertEqual(
            resolve_article_image(
                article, placement=placement, site_settings=site_settings
            ).alt,
            "Placement image alt",
        )
        placement.override_image_alt = ""
        self.assertEqual(
            resolve_article_image(
                article, placement=placement, site_settings=site_settings
            ).alt,
            "Article image alt",
        )
        article.featured_image_alt = ""
        self.assertEqual(
            resolve_article_image(
                article, placement=placement, site_settings=site_settings
            ).alt,
            "Image title",
        )
