import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation
from PIL import Image as PillowImage
from wagtail.documents import get_document_model
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.models import Journal
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import (
    ContentColumnConfig,
    NavigationItem,
    NavigationTargetType,
)
from ai_author_forum.standardpages.models import StandardPage
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider
from ai_author_forum.static_publish.readiness import ContentReadinessResult
from ai_author_forum.static_publish.services import (
    AssetReferenceParser,
    StaticPublisher,
    safe_relative_path,
)


class StaticFrontendTests(TestCase):
    def setUp(self):
        readiness_patcher = patch(
            "ai_author_forum.static_publish.services.check_content_readiness",
            return_value=ContentReadinessResult(configured=True),
        )
        readiness_patcher.start()
        self.addCleanup(readiness_patcher.stop)

    @classmethod
    def setUpTestData(cls):
        cls.root = HomePage.objects.first() or Page.get_first_root_node()
        cls.journal = Journal.objects.create(
            name="AI Ethics Forum",
            name_cn="AI 伦理论坛",
            slug="ai-ethics-forum",
            az_group="A",
            status="active",
            homepage_intro="Responsible authorship and publishing.",
        )
        cls.other_journal = Journal.objects.create(
            name="Other Forum",
            slug="other-forum",
            az_group="O",
            status="active",
        )
        cls.paused_journal = Journal.objects.create(
            name="Paused Forum",
            slug="paused-forum",
            az_group="P",
            status="paused",
        )
        cls.article_a = cls.create_article("Alpha article", "alpha-article")
        cls.article_b = cls.create_article("Beta article", "beta-article")
        cls.article_c = cls.create_article("Gamma article", "gamma-article")

    @classmethod
    def create_article(cls, title, slug):
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract=f"{title} abstract",
            body=[("paragraph", f"<p>{title} body</p>")],
            authors="Editorial team",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            primary_journal=cls.journal,
            keywords="AI authorship",
            review_status=ArticlePage.ReviewStatus.APPROVED,
        )
        cls.root.add_child(instance=article)
        article.save_revision().publish()
        return article

    def place(self, article, slot_code, target_type, target_slug, **kwargs):
        return ArticlePlacement.objects.create(
            article=article,
            slot=LayoutSlot.objects.get(code=slot_code),
            target_type=target_type,
            target_slug=target_slug,
            **kwargs,
        )

    def test_homepage_is_available(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<main", html=False)
        self.assertContains(response, self.root.title)
        sponsorship = BeautifulSoup(response.content, "html.parser").select_one(
            ".c-footer__sponsorship"
        )
        self.assertIsNotNone(sponsorship)
        self.assertEqual(
            sponsorship.get_text(" ", strip=True),
            "Sponsored by University of Tennessee Health Science Center",
        )

    def test_managed_navigation_information_page_is_available_and_sponsored(self):
        item = NavigationItem.objects.get(
            group__navigation_set__journal=self.journal,
            target_type=NavigationTargetType.INTERNAL_PATH,
            code="journal-information",
        )

        response = self.client.get(item.target_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{self.journal.name_cn}：期刊信息")
        sponsorship = BeautifulSoup(response.content, "html.parser").select_one(
            ".c-footer__sponsorship"
        )
        self.assertIsNotNone(sponsorship)
        self.assertEqual(
            sponsorship.get_text(" ", strip=True),
            "Sponsored by University of Tennessee Health Science Center",
        )

    def test_editorial_page_uses_wagtail_content_and_publishes_fixed_html(self):
        careers = StandardPage.objects.get(slug="careers")
        careers.introduction = "Editor-maintained careers introduction."
        careers.body = [
            (
                "rich_text",
                "<h2>Career pathways</h2><p>Approved careers guidance.</p>",
            )
        ]
        careers.save_revision().publish()
        careers.refresh_from_db()

        navigation_item = NavigationItem.objects.get(
            code="careers",
            target_type=NavigationTargetType.WAGTAIL_PAGE,
            page_id=careers.pk,
        )
        self.assertEqual(navigation_item.target_url, "/explore-content/careers/")

        response = self.client.get(navigation_item.target_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editor-maintained careers introduction.")
        self.assertContains(response, "Career pathways")
        self.assertContains(response, "Approved careers guidance.")
        rendered = response.content.decode()
        self.assertNotIn("This simulated page", rendered)
        self.assertNotIn("Content pending final editorial copy", rendered)
        self.assertNotIn("managed placeholder", rendered)

        target = next(
            target
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_id == f"page:{careers.pk}"
        )
        self.assertEqual(target.url, navigation_item.target_url)
        self.assertEqual(target.target_type, "wagtail_page")

        with TemporaryDirectory() as output_root:
            publisher = StaticPublisher(output_root)
            job = StaticPublishJob.objects.create(scope=StaticPublishJob.Scope.FULL)
            publisher.build(job)
            page_html = Path(
                output_root,
                "current",
                "explore-content",
                "careers",
                "index.html",
            ).read_text(encoding="utf-8")

        self.assertIn("Editor-maintained careers introduction.", page_html)
        self.assertIn("Career pathways", page_html)
        self.assertIn("Approved careers guidance.", page_html)
        sponsorship = BeautifulSoup(page_html, "html.parser").select_one(
            ".c-footer__sponsorship"
        )
        self.assertIsNotNone(sponsorship)
        self.assertEqual(
            sponsorship.get_text(" ", strip=True),
            "Sponsored by University of Tennessee Health Science Center",
        )
        self.assertNotIn("This simulated page", page_html)
        self.assertNotIn("Content pending final editorial copy", page_html)
        self.assertNotIn("managed placeholder", page_html)

    def test_homepage_slots_render_and_publish_to_static_index(self):
        def create_image(filename, title, color):
            image_data = BytesIO()
            PillowImage.new("RGB", (1800, 1040), color).save(image_data, format="PNG")
            return CustomImage.objects.create(
                title=title,
                file=SimpleUploadedFile(
                    filename,
                    image_data.getvalue(),
                    content_type="image/png",
                ),
            )

        with TemporaryDirectory() as media_root, TemporaryDirectory() as output_root:
            with self.settings(MEDIA_ROOT=media_root):
                cache.clear()
                self.article_a.refresh_from_db()
                hero_image = create_image(
                    "homepage-hero.png", "Hero image title", "navy"
                )
                story_override_image = create_image(
                    "story-placement-override.png",
                    "Story placement image title",
                    "teal",
                )
                story_article_image = create_image(
                    "story-article-cover.png", "Story article image title", "maroon"
                )
                self.article_a.featured_image = hero_image
                self.article_a.featured_image_alt = "Hero article cover alt"
                self.article_a.save_revision().publish()
                ArticlePage.objects.filter(pk=self.article_c.pk).update(
                    featured_image=story_article_image,
                    featured_image_alt="Story article cover alt",
                    abstract="",
                )
                self.article_c.refresh_from_db()

                hero = self.place(
                    self.article_a,
                    "home_hero",
                    ArticlePlacement.TargetType.MAIN_SITE,
                    "",
                    override_title="Home hero headline",
                    override_summary="Home hero controlled summary",
                )
                story_override = self.place(
                    self.article_b,
                    "home_visual_stories",
                    ArticlePlacement.TargetType.MAIN_SITE,
                    "",
                    override_title="Visual story override headline",
                    override_summary="Visual story controlled summary",
                    override_image=story_override_image,
                    override_image_alt="Placement-specific visual story alt",
                )
                long_title = "Long visual story headline " + (
                    "with controlled wrapping " * 8
                )
                story_article = self.place(
                    self.article_c,
                    "home_visual_stories",
                    ArticlePlacement.TargetType.MAIN_SITE,
                    "",
                    override_title=long_title,
                )

                response = self.client.get("/")
                self.assertEqual(response.status_code, 200)
                rendered = response.content.decode("utf-8")
                soup = BeautifulSoup(rendered, "html.parser")

                hero_section = soup.select_one('section[data-home-slot="home_hero"]')
                self.assertIsNotNone(hero_section)
                hero_card = hero_section.select_one(
                    f'article[data-placement-article="{self.article_a.static_slug}"]'
                )
                self.assertIsNotNone(hero_card)
                hero_links = hero_card.find_all(
                    "a", href=self.article_a.get_absolute_url()
                )
                self.assertEqual(len(hero_links), 2)
                self.assertEqual(
                    hero_card.select_one("img")["alt"], "Hero article cover alt"
                )
                self.assertEqual(
                    hero_card.select_one("h1 a").get_text(strip=True),
                    hero.display_title,
                )

                visual_section = soup.select_one(
                    'section[data-home-slot="home_visual_stories"]'
                )
                self.assertIsNotNone(visual_section)
                self.assertEqual(
                    visual_section.select_one(".c-section-heading h2").get_text(
                        strip=True
                    ),
                    "研究亮点",
                )
                visual_cards = visual_section.select("article.c-visual-story-card")
                self.assertEqual(len(visual_cards), 2)
                expected_visuals = (
                    (
                        story_override,
                        "Placement-specific visual story alt",
                        True,
                    ),
                    (story_article, "Story article cover alt", False),
                )
                for placement, expected_alt, has_summary in expected_visuals:
                    card = visual_section.select_one(
                        f'article[data-placement-article="{placement.article.static_slug}"]'
                    )
                    self.assertIsNotNone(card)
                    article_url = placement.article.get_absolute_url()
                    links = card.find_all("a", href=article_url)
                    self.assertEqual(len(links), 2)
                    self.assertEqual(card.select_one("img")["alt"], expected_alt)
                    self.assertTrue(all(link.get("tabindex") != "-1" for link in links))
                    summary = card.select_one(
                        ".c-visual-story-card__body > p:not(.c-visual-story-card__meta)"
                    )
                    self.assertEqual(summary is not None, has_summary)

                long_title_card = visual_section.select_one(
                    f'article[data-placement-article="{story_article.article.static_slug}"]'
                )
                self.assertEqual(
                    long_title_card.select_one("h3 a").get_text(" ", strip=True),
                    long_title.strip(),
                )
                for legacy_placeholder in (
                    "journal-cover.png",
                    "metrics-chart.png",
                    "Replaceable journal cover",
                    "Statistics chart placeholder",
                ):
                    self.assertNotIn(legacy_placeholder, rendered)

                publisher = StaticPublisher(output_root)
                job = StaticPublishJob.objects.create(scope=StaticPublishJob.Scope.FULL)
                publisher.build(job)
                home_path = Path(output_root, "current", "index.html")
                article_path = Path(
                    output_root,
                    "current",
                    self.article_a.get_static_output_path().lstrip("/"),
                )
                with self.assertNumQueries(0):
                    home_html = home_path.read_text(encoding="utf-8")
                    article_html = article_path.read_text(encoding="utf-8")

        static_soup = BeautifulSoup(home_html, "html.parser")
        self.assertEqual(
            len(static_soup.select('section[data-home-slot="home_hero"] article')),
            1,
        )
        self.assertEqual(
            len(
                static_soup.select(
                    'section[data-home-slot="home_visual_stories"] article'
                )
            ),
            2,
        )
        self.assertIn("Home hero headline", home_html)
        self.assertIn("Visual story override headline", home_html)
        self.assertIn(long_title.strip(), home_html)
        self.assertIn(self.article_a.title, article_html)

    def test_homepage_missing_image_uses_controlled_nonlegacy_placeholder(self):
        placement = self.place(
            self.article_a,
            "home_hero",
            ArticlePlacement.TargetType.MAIN_SITE,
            "",
            override_title="Controlled no-image hero",
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        hero = soup.select_one('section[data-home-slot="home_hero"] article')
        image = hero.select_one("img")

        self.assertTrue(image["src"].endswith("/static/images/reference/article-1.png"))
        self.assertEqual(image["alt"], placement.article.title)
        self.assertNotIn("journal-cover.png", response.content.decode("utf-8"))
        self.assertNotIn("metrics-chart.png", response.content.decode("utf-8"))

    def test_homepage_auto_fill_slot_renders_and_publishes(self):
        featured_slot = LayoutSlot.objects.get(code="home_featured")
        featured_slot.fill_mode = LayoutSlot.FillMode.AUTO
        featured_slot.max_items = 2
        featured_slot.save(update_fields=("fill_mode", "max_items"))
        self.place(
            self.article_a,
            "section_article_list",
            ArticlePlacement.TargetType.SECTION,
            "ai-article",
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-home-slot="home_featured"', html=False)
        self.assertContains(response, 'data-auto-placement="true"', html=False)
        self.assertContains(response, self.article_a.title)

        with TemporaryDirectory() as output_root:
            publisher = StaticPublisher(output_root)
            job = StaticPublishJob.objects.create(scope=StaticPublishJob.Scope.FULL)
            publisher.build(job)
            home_html = Path(output_root, "current", "index.html").read_text(
                encoding="utf-8"
            )

        self.assertIn('data-auto-placement="true"', home_html)
        self.assertIn(self.article_a.title, home_html)

    def test_a_z_and_journal_pages_only_show_active_targeted_content(self):
        self.place(
            self.article_a,
            "journal_latest",
            ArticlePlacement.TargetType.JOURNAL,
            self.journal.slug,
        )
        self.place(
            self.article_b,
            "journal_latest",
            ArticlePlacement.TargetType.JOURNAL,
            self.other_journal.slug,
        )

        index_response = self.client.get(reverse("journal_index"))
        self.assertContains(index_response, self.journal.name)
        self.assertContains(index_response, self.other_journal.name)
        self.assertNotContains(index_response, self.paused_journal.name)

        detail_response = self.client.get(
            reverse("journal_detail", kwargs={"slug": self.journal.slug})
        )
        self.assertContains(detail_response, self.article_a.title)
        self.assertNotContains(detail_response, self.article_b.title)
        self.assertContains(detail_response, self.article_a.get_absolute_url())
        self.assertNotContains(detail_response, self.article_a.get_static_output_path())

    def test_journal_page_renders_highlights_slot_as_featured_content(self):
        self.place(
            self.article_a,
            "journal_highlights",
            ArticlePlacement.TargetType.JOURNAL,
            self.journal.slug,
            override_title="Highlighted journal placement",
        )

        response = self.client.get(
            reverse("journal_detail", kwargs={"slug": self.journal.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Highlighted journal placement")
        self.assertNotContains(
            response, "No placed articles are currently available for this journal."
        )

    def test_english_journal_page_uses_localized_labels_and_deduplicated_articles(self):
        self.place(
            self.article_a,
            "journal_hero",
            ArticlePlacement.TargetType.JOURNAL,
            self.journal.slug,
        )
        self.place(
            self.article_a,
            "journal_latest",
            ArticlePlacement.TargetType.JOURNAL,
            self.journal.slug,
        )
        self.place(
            self.article_b,
            "journal_latest",
            ArticlePlacement.TargetType.JOURNAL,
            self.journal.slug,
        )

        with translation.override("en"):
            response = self.client.get(f"/en/journals/{self.journal.slug}/")

        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode("utf-8")
        soup = BeautifulSoup(rendered, "html.parser")
        self.assertEqual(
            soup.select_one("#journal-home-heading").get_text(strip=True),
            self.journal.name,
        )
        self.assertEqual(len(soup.select(".c-journal-home__featured-story article")), 1)
        self.assertEqual(len(soup.select(".c-journal-home__article-grid article")), 1)
        self.assertIn("AI Article", rendered)
        self.assertNotIn("AI 文章", rendered)
        self.assertNotIn("Replaceable journal cover", rendered)

    def test_section_page_uses_exact_section_target_and_display_order(self):
        later = self.place(
            self.article_a,
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "news",
            sort_order=20,
        )
        pinned = self.place(
            self.article_b,
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "news",
            sort_order=99,
            is_pinned=True,
        )
        self.place(
            self.article_c,
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "opinion",
        )

        response = self.client.get(
            reverse("main_content_column_detail", kwargs={"column_slug": "news"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.article_c.title)
        content = response.content.decode("utf-8")
        self.assertLess(
            content.index(pinned.display_title), content.index(later.display_title)
        )

    def test_content_column_static_type_year_and_combined_filters(self):
        news_article = self.create_article(
            "Filtered news article", "filtered-news-article"
        )
        news_article.article_type = ArticlePage.ArticleType.NEWS
        news_article.save(update_fields=("article_type",))
        ArticlePage.objects.filter(pk=self.article_a.pk).update(
            first_published_at=timezone.make_aware(datetime(2025, 6, 1, 12, 0))
        )
        ArticlePage.objects.filter(pk=news_article.pk).update(
            first_published_at=timezone.make_aware(datetime(2024, 6, 1, 12, 0))
        )
        self.place(
            self.article_a,
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "ai-article",
        )
        self.place(
            news_article,
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "ai-article",
        )

        base_url = reverse(
            "main_content_column_detail", kwargs={"column_slug": "ai-article"}
        )
        type_url = reverse(
            "main_content_column_type",
            kwargs={"column_slug": "ai-article", "article_type": "news"},
        )
        year_url = reverse(
            "main_content_column_year",
            kwargs={"column_slug": "ai-article", "year": 2025},
        )
        combined_url = reverse(
            "main_content_column_type_year",
            kwargs={
                "column_slug": "ai-article",
                "article_type": "news",
                "year": 2024,
            },
        )

        response = self.client.get(base_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="column-article-type"', html=False)
        self.assertContains(response, 'id="column-year"', html=False)
        self.assertContains(response, f'value="{type_url}"', html=False)
        self.assertContains(response, f'value="{year_url}"', html=False)
        self.assertContains(response, f'<a href="{type_url}"', html=False)
        self.assertContains(response, f'<a href="{year_url}"', html=False)
        self.assertNotContains(response, "fetch(")
        self.assertNotContains(response, "/api/")

        response = self.client.get(type_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, news_article.title)
        self.assertNotContains(response, self.article_a.title)

        response = self.client.get(year_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.article_a.title)
        self.assertNotContains(response, news_article.title)

        response = self.client.get(combined_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, news_article.title)
        self.assertNotContains(response, self.article_a.title)
        self.assertContains(response, f'value="{combined_url}"', html=False)

    def test_disabled_content_column_filter_urls_return_404(self):
        type_url = reverse(
            "main_content_column_type",
            kwargs={"column_slug": "news", "article_type": "news"},
        )
        self.assertEqual(self.client.get(type_url).status_code, 404)

        config = ContentColumnConfig.objects.get(
            navigation_item__group__navigation_set__journal__isnull=True,
            navigation_item__code="ai-article",
        )
        config.enable_year_filter = False
        config.save(update_fields=("enable_year_filter",))
        year_url = reverse(
            "main_content_column_year",
            kwargs={"column_slug": "ai-article", "year": 2025},
        )
        self.assertEqual(self.client.get(year_url).status_code, 404)

    def test_static_search_indexes_all_placed_articles_without_live_querying(self):
        self.place(
            self.article_a,
            "search_recommended",
            ArticlePlacement.TargetType.SEARCH,
            "search",
        )
        self.place(
            self.article_b,
            "section_article_list",
            ArticlePlacement.TargetType.SECTION,
            "news",
        )

        response = self.client.get(reverse("search"), {"q": "Beta"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="recommended-topics-title"', html=False)
        self.assertContains(response, 'id="static-search-index"', html=False)
        self.assertContains(response, self.article_a.title)
        self.assertContains(response, self.article_b.title)
        self.assertNotContains(response, self.article_c.title)
        self.assertNotContains(response, "result for Beta")

    def test_unplaced_article_is_not_available_at_canonical_static_route(self):
        response = self.client.get(
            reverse("article_detail", kwargs={"slug": self.article_a.static_slug})
        )
        self.assertEqual(response.status_code, 404)

        self.place(
            self.article_a,
            "section_article_list",
            ArticlePlacement.TargetType.SECTION,
            "ai-article",
        )
        response = self.client.get(
            reverse("article_detail", kwargs={"slug": self.article_a.static_slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.article_a.title)

    def test_default_provider_discovers_all_formal_static_objects(self):
        self.place(
            self.article_a,
            "section_article_list",
            ArticlePlacement.TargetType.SECTION,
            "ai-article",
        )
        targets = WagtailPageTargetProvider().get_targets()
        paths = {target.output_path for target in targets}
        search_target = next(
            target for target in targets if target.target_type == "search_page"
        )

        self.assertIn("journals/index.html", paths)
        self.assertIn(f"journals/{self.journal.slug}/index.html", paths)
        self.assertIn("explore-content/ai-article/index.html", paths)
        self.assertIn("explore-content/news/index.html", paths)
        self.assertIn(f"articles/{self.article_a.static_slug}/index.html", paths)
        self.assertIn("search/index.html", paths)
        self.assertEqual(search_target.dependencies["article_ids"], [self.article_a.pk])
        self.assertTrue(search_target.dependencies["placement_ids"])
        self.assertNotIn(f"articles/{self.article_b.static_slug}/index.html", paths)

    def test_default_publisher_writes_and_validates_all_formal_pages(self):
        acceptance_article = self.create_article(
            "Frontend acceptance article", "frontend-acceptance-article"
        )
        with TemporaryDirectory() as output_root, TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                image_data = BytesIO()
                PillowImage.new("RGB", (64, 48), "navy").save(image_data, format="PNG")
                image = CustomImage.objects.create(
                    title="Frontend acceptance image",
                    file=SimpleUploadedFile(
                        "frontend-acceptance.png",
                        image_data.getvalue(),
                        content_type="image/png",
                    ),
                )
                document = get_document_model().objects.create(
                    title="Frontend acceptance document",
                    file=SimpleUploadedFile(
                        "frontend-acceptance.txt",
                        b"Static document body",
                        content_type="text/plain",
                    ),
                )
                acceptance_article.body = [
                    ("paragraph", "<p>Alpha article body</p>"),
                    ("heading", "Structured results"),
                    (
                        "image",
                        {
                            "image": image,
                            "alt_text": "Acceptance chart",
                            "caption": "Frontend acceptance image",
                        },
                    ),
                    (
                        "quote",
                        {
                            "quote": "Static publishing preserves structure.",
                            "attribution": "Acceptance suite",
                        },
                    ),
                    (
                        "list",
                        {
                            "list_type": "ordered",
                            "items": ["<p>First item</p>", "<p>Second item</p>"],
                        },
                    ),
                    (
                        "table",
                        {
                            "data": [["Metric", "Value"], ["Coverage", "100%"]],
                            "first_row_is_table_header": True,
                            "first_col_is_header": False,
                            "table_header_choice": "row",
                            "table_caption": "Acceptance metrics",
                        },
                    ),
                    (
                        "document",
                        {
                            "document": document,
                            "link_text": "Download acceptance document",
                            "description": "A copied static media asset.",
                        },
                    ),
                    ("html", "<p data-legacy-body>Legacy body remains supported.</p>"),
                ]
                acceptance_article.save(
                    clean=False,
                    bypass_article_permission_check=True,
                    update_fields=("body",),
                )
                self.place(
                    acceptance_article,
                    "section_article_list",
                    ArticlePlacement.TargetType.SECTION,
                    "ai-article",
                    override_image=image,
                )
                publisher = StaticPublisher(output_root)
                job = StaticPublishJob.objects.create(scope=StaticPublishJob.Scope.FULL)
                manifest_record = publisher.build(job)
                current = Path(output_root, "current")

                expected = (
                    "index.html",
                    "journals/index.html",
                    f"journals/{self.journal.slug}/index.html",
                    "explore-content/ai-article/index.html",
                    f"articles/{acceptance_article.static_slug}/index.html",
                    "search/index.html",
                    "manifest.json",
                )
                for relative_path in expected:
                    self.assertTrue((current / relative_path).is_file(), relative_path)

                local_assets = set()
                for html_file in current.rglob("*.html"):
                    parser = AssetReferenceParser()
                    parser.feed(html_file.read_text(encoding="utf-8"))
                    for reference in parser.references:
                        path = urlsplit(reference).path
                        if not path.startswith(
                            (settings.STATIC_URL, settings.MEDIA_URL)
                        ):
                            continue
                        relative = safe_relative_path(path)
                        local_assets.add(relative.as_posix())
                        self.assertTrue(
                            (current / relative).is_file(),
                            f"{html_file.relative_to(current)} -> {path}",
                        )

                article_html = (
                    current / "articles" / acceptance_article.static_slug / "index.html"
                ).read_text(encoding="utf-8")
                self.assertIn('class="c-article-content-list"', article_html)
                self.assertIn('class="c-article-table"', article_html)
                self.assertIn('class="c-article-document"', article_html)
                self.assertIn("data-legacy-body", article_html)
                self.assertIn(document.file.url, article_html)
                self.assertNotIn(document.url, article_html)

                suffixes = {Path(asset).suffix.lower() for asset in local_assets}
                self.assertIn(".css", suffixes)
                self.assertIn(".js", suffixes)
                self.assertTrue(
                    suffixes.intersection({".png", ".jpg", ".jpeg", ".webp"})
                )
                self.assertIn(".txt", suffixes)

                manifest = json.loads(
                    (current / "manifest.json").read_text(encoding="utf-8")
                )
                successful_pages = job.targets.filter(status="succeeded").count()
                self.assertEqual(manifest["version"], job.version)
                self.assertEqual(manifest["summary"]["pages"], successful_pages)
                self.assertEqual(manifest["summary"]["failed"], 0)
                self.assertEqual(
                    manifest_record.metadata["summary"], manifest["summary"]
                )
                self.assertTrue(manifest["asset_references"])

    def test_rollback_restores_formal_page_content_and_manifest(self):
        placement = self.place(
            self.article_a,
            "column_list",
            ArticlePlacement.TargetType.SECTION,
            "news",
            override_title="Release one headline",
        )
        with TemporaryDirectory() as output_root:
            publisher = StaticPublisher(output_root)
            first_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL
            )
            publisher.build(first_job)
            page_path = Path(output_root, "current", "sections", "news", "index.html")
            first_content = page_path.read_text(encoding="utf-8")
            self.assertIn("Release one headline", first_content)

            placement.override_title = "Release two headline"
            placement.save(update_fields=("override_title",))
            second_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL
            )
            publisher.build(second_job)
            second_content = page_path.read_text(encoding="utf-8")
            self.assertIn("Release two headline", second_content)
            self.assertNotEqual(first_content, second_content)

            publisher.rollback(first_job.version, reason="rollback regression fixture")

            restored_content = page_path.read_text(encoding="utf-8")
            restored_manifest = json.loads(
                Path(output_root, "current", "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(restored_content, first_content)
            self.assertEqual(restored_manifest["version"], first_job.version)
            self.assertNotEqual(restored_manifest["version"], second_job.version)
