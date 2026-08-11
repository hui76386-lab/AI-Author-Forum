from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image as PillowImage
from wagtail.models import Page

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import (
    NavigationItem,
    NavigationItemPathRedirect,
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
    NavigationTargetType,
)
from ai_author_forum.site_settings.navigation import ensure_main_navigation_set
from ai_author_forum.standardpages.models import StandardPage
from ai_author_forum.static_publish.providers import (
    PublishTarget,
    WagtailPageTargetProvider,
    output_path_for_url,
)
from ai_author_forum.static_publish.services import get_journal_publish_paths
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)


class WagtailPageTargetProviderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = HomePage.objects.first() or Page.get_first_root_node()
        cls.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="provider-test-admin",
                email="provider-test-admin@example.com",
                display_name="Provider Test Admin",
                password="test-password",
                is_staff=True,
            )
        )
        cls.journal = Journal.objects.create(
            name="Provider Journal",
            slug="provider-journal",
            az_group="P",
        )
        cls.slot = LayoutSlot.objects.get(code="home_featured")
        cls.article = ArticlePage(
            title="Provider Article",
            slug="provider-article",
            static_slug="provider-article",
            abstract="Provider abstract",
            body=[("paragraph", "<p>Provider body</p>")],
            authors="Provider author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=cls.journal,
            keywords="provider",
        )
        cls.root.add_child(instance=cls.article)
        cls.article.save_revision().publish()
        formally_approve_test_article(cls.article, actor=cls.admin)
        cls.article.refresh_from_db()
        cls.category = JournalCategory.objects.create(
            journal=cls.journal,
            name="Provider Category",
            code="provider-category",
            slug="provider-category",
        )
        cls.main_navigation_set = ensure_main_navigation_set()
        cls.journal_navigation_set = NavigationSet.objects.get(
            journal=cls.journal,
            scope=NavigationScope.JOURNAL,
            status=NavigationSetStatus.ACTIVE,
        )

    def create_article(self, title, *, article_type=ArticlePage.ArticleType.NEWS):
        slug = title.lower().replace(" ", "-")
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract=f"{title} abstract",
            body=[("paragraph", f"<p>{title} body</p>")],
            authors="Provider author",
            article_type=article_type,
            primary_journal=self.journal,
            keywords="provider",
        )
        self.root.add_child(instance=article)
        article.save_revision().publish()
        formally_approve_test_article(article, actor=self.admin)
        return ArticlePage.objects.get(pk=article.pk)

    def test_target_discovery_uses_one_publication_time_snapshot(self):
        provider = WagtailPageTargetProvider()
        snapshot = timezone.now()
        with (
            patch(
                "ai_author_forum.static_publish.providers.timezone.now",
                return_value=snapshot,
            ) as now,
            patch.object(provider, "_all_targets", return_value=[]) as all_targets,
        ):
            self.assertEqual(provider.get_targets(), [])
        now.assert_called_once_with()
        all_targets.assert_called_once_with(publication_time=snapshot)

    def test_article_requires_active_placement_before_static_publish(self):
        provider = WagtailPageTargetProvider()
        canonical_url = f"/articles/{self.article.static_slug}/"
        urls = {target.url for target in provider.get_targets()}
        self.assertNotIn(canonical_url, urls)
        self.assertNotIn(f"/en{canonical_url}", urls)

        ArticlePlacement.objects.create(
            article=self.article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
        )
        urls = {target.url for target in provider.get_targets()}
        self.assertIn(canonical_url, urls)
        self.assertIn(f"/en{canonical_url}", urls)

    def test_article_placement_must_be_in_schedule_with_active_slot_and_journal(self):
        provider = WagtailPageTargetProvider()
        canonical_url = f"/articles/{self.article.static_slug}/"
        now = timezone.now()
        placement = ArticlePlacement.objects.create(
            article=self.article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
            starts_at=now + timedelta(hours=1),
        )

        self.assertNotIn(
            canonical_url, {target.url for target in provider.get_targets()}
        )

        placement.starts_at = now - timedelta(hours=1)
        placement.ends_at = now + timedelta(hours=1)
        placement.save(update_fields=("starts_at", "ends_at"))
        self.assertIn(canonical_url, {target.url for target in provider.get_targets()})

        placement.ends_at = now - timedelta(minutes=1)
        placement.save(update_fields=("ends_at",))
        self.assertNotIn(
            canonical_url, {target.url for target in provider.get_targets()}
        )

        placement.ends_at = now + timedelta(hours=1)
        placement.is_active = False
        placement.save(update_fields=("ends_at", "is_active"))
        self.assertNotIn(
            canonical_url, {target.url for target in provider.get_targets()}
        )

        placement.is_active = True
        placement.save(update_fields=("is_active",))
        self.slot.is_active = False
        self.slot.save(update_fields=("is_active",))
        self.assertNotIn(
            canonical_url, {target.url for target in provider.get_targets()}
        )

        self.slot.is_active = True
        self.slot.save(update_fields=("is_active",))
        self.journal.status = "paused"
        self.journal.save(update_fields=("status",))
        self.assertNotIn(
            canonical_url, {target.url for target in provider.get_targets()}
        )

    def test_static_publish_targets_include_default_and_english_urls(self):
        ArticlePlacement.objects.create(
            article=self.article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
        )
        targets = WagtailPageTargetProvider().get_targets()
        by_url = {target.url: target for target in targets}

        expected_pairs = [
            ("/", "/en/"),
            ("/journals/", "/en/journals/"),
            ("/search/", "/en/search/"),
            (
                f"/articles/{self.article.static_slug}/",
                f"/en/articles/{self.article.static_slug}/",
            ),
        ]
        for default_url, english_url in expected_pairs:
            with self.subTest(default_url=default_url, english_url=english_url):
                self.assertIn(default_url, by_url)
                self.assertIn(english_url, by_url)
                self.assertEqual(
                    by_url[default_url].output_path,
                    output_path_for_url(default_url),
                )
                self.assertEqual(
                    by_url[english_url].output_path,
                    output_path_for_url(english_url),
                )
                self.assertTrue(by_url[english_url].source.endswith(":lang:en"))
                self.assertTrue(by_url[english_url].target_id.endswith(":lang:en"))

    def test_journal_publish_paths_include_public_directory_in_both_languages(self):
        paths = set(get_journal_publish_paths(self.journal))

        self.assertTrue(
            {
                "journals/index.html",
                "en/journals/index.html",
                f"journals/{self.journal.slug}/index.html",
                f"en/journals/{self.journal.slug}/index.html",
            }.issubset(paths),
            paths,
        )

    def test_navigation_dependencies_follow_main_and_journal_scope(self):
        ArticlePlacement.objects.create(
            article=self.article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
        )
        targets = WagtailPageTargetProvider().get_targets()
        by_url = {target.url: target for target in targets}

        main_urls = [
            "/",
            "/en/",
            "/journals/",
            "/en/journals/",
            "/search/",
            "/en/search/",
            "/explore-content/news/",
            "/en/explore-content/news/",
        ]
        for url in main_urls:
            with self.subTest(url=url):
                self.assertIn(
                    self.main_navigation_set.pk,
                    by_url[url].dependencies["navigation_set_ids"],
                )

        journal_urls = [
            f"/journals/{self.journal.slug}/",
            f"/en/journals/{self.journal.slug}/",
            self.category.get_absolute_url(),
            f"/en{self.category.get_absolute_url()}",
            f"/articles/{self.article.static_slug}/",
            f"/en/articles/{self.article.static_slug}/",
        ]
        for url in journal_urls:
            with self.subTest(url=url):
                self.assertIn(
                    self.journal_navigation_set.pk,
                    by_url[url].dependencies["navigation_set_ids"],
                )
                self.assertNotIn(
                    self.main_navigation_set.pk,
                    by_url[url].dependencies["navigation_set_ids"],
                )

    def test_internal_navigation_targets_are_published_with_scope_dependencies(self):
        item = NavigationItem.objects.get(
            group__navigation_set=self.journal_navigation_set,
            target_type=NavigationTargetType.INTERNAL_PATH,
            code="journal-information",
        )

        targets = WagtailPageTargetProvider().get_targets()
        target = next(
            target
            for target in targets
            if target.target_id == f"navigation_item:{item.pk}"
        )
        english_target = next(
            target
            for target in targets
            if target.target_id == f"navigation_item:{item.pk}:lang:en"
        )

        self.assertEqual(target.url, item.target_url)
        self.assertEqual(target.target_type, "managed_navigation_info")
        self.assertEqual(target.dependencies["journal_ids"], [self.journal.pk])
        self.assertEqual(
            target.dependencies["navigation_set_ids"], [self.journal_navigation_set.pk]
        )
        self.assertEqual(target.dependencies["navigation_item_ids"], [item.pk])
        self.assertEqual(english_target.url, f"/en{item.target_url}")

    def test_navigation_redirect_dependencies_include_set_and_item(self):
        item = (
            self.journal_navigation_set.groups.order_by("sort_order", "pk")
            .first()
            .items.order_by("sort_order", "pk")
            .first()
        )
        redirect = NavigationItemPathRedirect.objects.create(
            navigation_item=item,
            old_path="/journals/provider-journal/legacy-column/",
            new_path=item.target_url,
            http_status=301,
        )

        target = next(
            target
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_id == f"navigation_redirect:{redirect.pk}"
        )
        self.assertEqual(
            target.dependencies["navigation_set_ids"],
            [self.journal_navigation_set.pk],
        )
        self.assertEqual(target.dependencies["navigation_item_ids"], [item.pk])

        english_target = next(
            target
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_id == f"navigation_redirect:{redirect.pk}:lang:en"
        )
        self.assertEqual(
            english_target.url,
            "/en/journals/provider-journal/legacy-column/",
        )
        self.assertEqual(english_target.redirect_to, f"/en{item.target_url}")
        self.assertEqual(
            english_target.dependencies["navigation_set_ids"],
            [self.journal_navigation_set.pk],
        )
        self.assertEqual(
            english_target.dependencies["navigation_item_ids"],
            [item.pk],
        )

    def test_wagtail_editorial_page_target_records_streamfield_image_dependency(self):
        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            image_data = BytesIO()
            PillowImage.new("RGB", (64, 48), "navy").save(image_data, format="PNG")
            image = CustomImage.objects.create(
                title="Editorial provider image",
                file=SimpleUploadedFile(
                    "editorial-provider.png",
                    image_data.getvalue(),
                    content_type="image/png",
                ),
            )
            page = StandardPage(
                title="Editorial provider page",
                slug="editorial-provider-page",
                introduction="Controlled editorial content.",
                body=[
                    (
                        "image",
                        {
                            "image": image,
                            "image_alt_text": "Editorial illustration",
                            "caption": "Approved illustration",
                        },
                    )
                ],
                live=True,
                show_in_menus=False,
            )
            self.root.add_child(instance=page)
            page.save_revision().publish()

            target = next(
                target
                for target in WagtailPageTargetProvider().get_targets()
                if target.target_id == f"page:{page.pk}"
            )

            self.assertEqual(target.target_type, "wagtail_page")
            self.assertEqual(target.dependencies["image_ids"], [image.pk])

    def test_every_target_has_complete_dependency_schema(self):
        expected_keys = {
            "journal_ids",
            "category_ids",
            "article_ids",
            "placement_ids",
            "content_column_config_ids",
            "image_ids",
            "issue_ids",
            "issue_article_ids",
            "navigation_set_ids",
            "navigation_item_ids",
        }

        for target in WagtailPageTargetProvider().get_targets():
            with self.subTest(target_id=target.target_id, url=target.url):
                self.assertEqual(set(target.dependencies), expected_keys)

    def test_article_dependencies_use_live_revision_categories(self):
        second_category = JournalCategory.objects.create(
            journal=self.journal,
            name="Second Provider Category",
            code="second-provider-category",
            slug="second-provider-category",
            path_cache="/second-provider-category/",
        )
        assignment = self.article.category_assignments.get(is_primary=True)
        live_category = assignment.category
        self.article.save_revision().publish()
        ArticleCategoryAssignment.objects.filter(pk=assignment.pk).update(
            category=second_category
        )

        article = ArticlePage.objects.select_related(
            "primary_journal", "live_revision"
        ).get(pk=self.article.pk)
        provider = WagtailPageTargetProvider()
        provider._prepare_article_dependency_index([article])

        self.assertEqual(
            provider._article_dependencies(article)["category_ids"],
            [live_category.pk],
        )

    def test_article_dependency_preparation_uses_bounded_queries(self):
        for index in range(3):
            article = self.create_article(f"Bulk Provider Article {index}")
            ArticlePlacement.objects.create(
                article=article,
                slot=self.slot,
                target_type=ArticlePlacement.TargetType.MAIN_SITE,
            )
        articles = list(
            ArticlePage.objects.filter(primary_journal=self.journal)
            .select_related("primary_journal", "live_revision")
            .order_by("pk")
        )
        provider = WagtailPageTargetProvider()

        with self.assertNumQueries(1):
            provider._prepare_article_dependency_index(articles)
            for article in articles:
                provider._article_dependencies(article)

    def test_column_pagination_keeps_shared_dependencies_and_slices_list(self):
        item = NavigationItem.objects.get(
            group__navigation_set=self.main_navigation_set,
            target_type=NavigationTargetType.CONTENT_COLUMN,
            code="news",
        )
        config = item.content_column_config
        config.page_size = 2
        config.enable_type_filter = False
        config.enable_year_filter = False
        config.save(
            update_fields=("page_size", "enable_type_filter", "enable_year_filter")
        )
        shared_placements = []
        for slot_code in ("column_featured", "column_secondary", "column_sidebar"):
            shared_placements.append(
                ArticlePlacement.objects.create(
                    article=self.article,
                    slot=LayoutSlot.objects.get(code=slot_code),
                    target_type=ArticlePlacement.TargetType.SECTION,
                    target_slug=item.placement_target_slug,
                )
            )
        list_placements = []
        for index in range(3):
            article = self.create_article(f"Paged Provider Article {index}")
            list_placements.append(
                ArticlePlacement.objects.create(
                    article=article,
                    slot=LayoutSlot.objects.get(code="column_list"),
                    target_type=ArticlePlacement.TargetType.SECTION,
                    target_slug=item.placement_target_slug,
                    sort_order=index,
                )
            )

        by_url = {
            target.url: target
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_type == "managed_content_column"
        }
        first = by_url[item.target_url]
        second = by_url[f"{item.target_url}page/2/"]
        shared_ids = {placement.pk for placement in shared_placements}
        first_list_ids = {placement.pk for placement in list_placements[:2]}
        second_list_ids = {list_placements[2].pk}

        self.assertTrue(shared_ids.issubset(first.dependencies["placement_ids"]))
        self.assertTrue(shared_ids.issubset(second.dependencies["placement_ids"]))
        self.assertTrue(first_list_ids.issubset(first.dependencies["placement_ids"]))
        self.assertTrue(first_list_ids.isdisjoint(second.dependencies["placement_ids"]))
        self.assertTrue(second_list_ids.issubset(second.dependencies["placement_ids"]))
        self.assertTrue(second_list_ids.isdisjoint(first.dependencies["placement_ids"]))

    def test_column_filter_targets_only_include_populated_combinations(self):
        item = NavigationItem.objects.get(
            group__navigation_set=self.main_navigation_set,
            target_type=NavigationTargetType.CONTENT_COLUMN,
            code="news",
        )
        config = item.content_column_config
        config.enable_type_filter = True
        config.enable_year_filter = True
        config.save(update_fields=("enable_type_filter", "enable_year_filter"))
        cases = (
            ("Filtered News 2026", ArticlePage.ArticleType.NEWS, 2026),
            ("Filtered Opinion 2025", ArticlePage.ArticleType.OPINION, 2025),
        )
        for title, article_type, year in cases:
            article = self.create_article(title, article_type=article_type)
            ArticlePage.objects.filter(pk=article.pk).update(
                first_published_at=timezone.now().replace(year=year)
            )
            ArticlePlacement.objects.create(
                article=article,
                slot=LayoutSlot.objects.get(code="column_list"),
                target_type=ArticlePlacement.TargetType.SECTION,
                target_slug=item.placement_target_slug,
            )

        urls = {
            target.url
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_type == "managed_content_column"
        }
        self.assertIn(f"{item.target_url}type/news/year/2026/", urls)
        self.assertIn(f"{item.target_url}type/opinion/year/2025/", urls)
        self.assertNotIn(f"{item.target_url}type/news/year/2025/", urls)
        self.assertNotIn(f"{item.target_url}type/opinion/year/2026/", urls)

    def test_requested_article_expands_to_all_targets_with_article_or_image_dependency(
        self,
    ):
        article_target = PublishTarget(
            "/articles/changed/",
            "article.changed",
            target_type="article_page",
            dependencies={"article_ids": [10], "image_ids": [20]},
        )
        homepage_target = PublishTarget(
            "/",
            "homepage",
            dependencies={"article_ids": [10]},
        )
        category_target = PublishTarget(
            "/category/",
            "category",
            dependencies={"article_ids": [10]},
        )
        journal_target = PublishTarget(
            "/journals/example/",
            "journal",
            dependencies={"image_ids": [20]},
        )
        unrelated_target = PublishTarget(
            "/unrelated/",
            "unrelated",
            dependencies={"article_ids": [99], "image_ids": [98]},
        )

        expanded = WagtailPageTargetProvider._expand_article_reverse_dependencies(
            [
                article_target,
                homepage_target,
                category_target,
                journal_target,
                unrelated_target,
            ],
            [article_target],
        )

        self.assertCountEqual(
            [target.url for target in expanded],
            [
                "/articles/changed/",
                "/",
                "/category/",
                "/journals/example/",
            ],
        )

    def test_homepage_request_does_not_fan_out_to_homepage_article_details(self):
        homepage_target = PublishTarget(
            "/",
            "homepage",
            dependencies={"article_ids": [10]},
        )
        article_target = PublishTarget(
            "/articles/changed/",
            "article.changed",
            target_type="article_page",
            dependencies={"article_ids": [10]},
        )

        expanded = WagtailPageTargetProvider._expand_article_reverse_dependencies(
            [homepage_target, article_target],
            [homepage_target],
        )

        self.assertEqual(expanded, [homepage_target])

    def test_actual_article_request_rebuilds_referencing_homepage(self):
        ArticlePlacement.objects.create(
            article=self.article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
        )
        provider = WagtailPageTargetProvider()
        article_url = f"/articles/{self.article.static_slug}/"

        selected_urls = {
            target.url for target in provider.get_targets(paths=[article_url])
        }
        homepage_only_urls = {
            target.url for target in provider.get_targets(paths=["/"])
        }

        self.assertIn(article_url, selected_urls)
        self.assertIn("/", selected_urls)
        self.assertNotIn(article_url, homepage_only_urls)
        self.assertEqual(homepage_only_urls, {"/", "/en/"})
