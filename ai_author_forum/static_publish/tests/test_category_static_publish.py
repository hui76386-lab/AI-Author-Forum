import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.models import Page

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalCategoryPathRedirect,
)
from ai_author_forum.placements.category_services import sync_category_placements
from ai_author_forum.static_publish.category_services import (
    CategoryPublicationConsistencyError,
    validate_category_publication_consistency,
)
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob
from ai_author_forum.static_publish.providers import (
    PublishTarget,
    WagtailPageTargetProvider,
)
from ai_author_forum.static_publish.services import PublishError, StaticPublisher
from ai_author_forum.test_helpers import (
    formally_approve_test_article,
    grant_business_super_admin,
)


@override_settings(
    STATIC_CATEGORY_PAGE_SIZE=1,
    STATIC_PUBLISH_KEEP_RELEASES=5,
    STATIC_PUBLISH_ENFORCE_CONTENT_READINESS=False,
)
class CategoryStaticPublishTests(TestCase):
    def setUp(self):
        from unittest.mock import patch

        snapshot_patcher = patch.object(
            StaticPublisher, "_configure_snapshot_transaction", return_value=None
        )
        snapshot_patcher.start()
        self.addCleanup(snapshot_patcher.stop)
        self.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="category-static-admin",
                email="category-static-admin@example.com",
                display_name="Category Static Admin",
                password="test-password",
                is_staff=True,
            )
        )
        self.journal = Journal.objects.create(
            name="Category Static Journal",
            slug="category-static-journal",
            az_group="C",
        )
        self.category = JournalCategory.objects.create(
            journal=self.journal,
            name="Research",
            code="RESEARCH",
            slug="research",
            depth=1,
            path_cache="research",
            show_in_navigation=True,
        )

    def create_live_article(self, title):
        article = ArticlePage(
            title=title,
            slug=title.lower().replace(" ", "-"),
            static_slug=title.lower().replace(" ", "-"),
            abstract="Category static abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=self.journal,
        )
        Page.get_first_root_node().add_child(instance=article)
        ArticleCategoryAssignment.objects.create(
            article=article,
            category=self.category,
            is_primary=True,
        )
        revision = article.save_revision(bypass_article_permission_check=True)
        revision.publish(skip_permission_checks=True)
        formally_approve_test_article(article, actor=self.admin)
        article.refresh_from_db()
        sync_category_placements(
            article_id=article.pk,
            revision_id=article.approved_version_id,
            actor=self.admin,
        )
        article.refresh_from_db()
        return article

    def test_provider_discovers_fixed_category_pages_pagination_and_redirect(self):
        first = self.create_live_article("Category Article One")
        second = self.create_live_article("Category Article Two")
        redirect = JournalCategoryPathRedirect.objects.create(
            category=self.category,
            journal=self.journal,
            old_path=("/journals/category-static-journal/categories/old-research/"),
            new_path=self.category.get_absolute_url(),
        )

        targets = WagtailPageTargetProvider().get_targets()
        by_id = {target.target_id: target for target in targets}

        page_one = by_id[f"category:{self.category.pk}:page:1"]
        page_two = by_id[f"category:{self.category.pk}:page:2"]
        redirect_target = by_id[f"category_redirect:{redirect.pk}"]
        self.assertEqual(page_one.url, self.category.get_absolute_url())
        self.assertEqual(page_two.url, f"{self.category.get_absolute_url()}page/2/")
        self.assertCountEqual(
            page_one.dependencies["article_ids"] + page_two.dependencies["article_ids"],
            [first.pk, second.pk],
        )
        self.assertTrue(page_one.dependencies["placement_ids"])
        self.assertEqual(redirect_target.action, "redirect")
        self.assertEqual(redirect_target.http_status, 301)
        self.assertEqual(redirect_target.redirect_to, self.category.get_absolute_url())

    def test_scoped_consistency_ignores_unpublished_categories_from_other_journals(
        self,
    ):
        other_journal = Journal.objects.create(
            name="Unpublished Other Journal",
            slug="unpublished-other-journal",
            az_group="U",
            status="active",
        )
        other_category = JournalCategory.objects.create(
            journal=other_journal,
            name="Other research",
            code="OTHER",
            slug="other",
            depth=1,
            path_cache="other",
            show_in_navigation=True,
        )
        target = PublishTarget(
            self.category.get_absolute_url(),
            f"journals.JournalCategory:{self.category.pk}:page:1",
            target_type="category_page",
            target_id=f"category:{self.category.pk}:page:1",
            dependencies={"journal_ids": [self.journal.pk], "category_ids": [self.category.pk]},
        )

        with TemporaryDirectory() as staging:
            result = validate_category_publication_consistency(
                version_id="scoped-category-release",
                targets=[target],
                staging=staging,
                journal_ids=[self.journal.pk],
            )
            self.assertEqual(result["status"], "valid")
            with self.assertRaisesMessage(
                CategoryPublicationConsistencyError,
                f"Category {other_category.pk} has no generated page-one output",
            ):
                validate_category_publication_consistency(
                    version_id="global-category-release",
                    targets=[target],
                    staging=staging,
                )

    def test_fixed_pagination_route_and_navigation_render(self):
        self.create_live_article("Category Article One")
        self.create_live_article("Category Article Two")

        first = self.client.get(self.category.get_absolute_url())
        second = self.client.get(f"{self.category.get_absolute_url()}page/2/")

        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "期刊主题")
        self.assertContains(first, f"{self.category.get_absolute_url()}page/2/")
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "第 2 页，共 2 页")
        self.assertNotContains(second, "?page=")

        page_one_alias = self.client.get(f"{self.category.get_absolute_url()}page/1/")
        self.assertEqual(page_one_alias.status_code, 301)
        self.assertEqual(
            page_one_alias.headers["Location"], self.category.get_absolute_url()
        )
        query_alias = self.client.get(self.category.get_absolute_url(), {"page": "2"})
        self.assertEqual(query_alias.status_code, 301)
        self.assertEqual(
            query_alias.headers["Location"],
            f"{self.category.get_absolute_url()}page/2/",
        )

    def test_publication_gate_blocks_redirect_chains(self):
        canonical = self.category.get_absolute_url()
        first_old = "/journals/category-static-journal/categories/first-old/"
        second_old = "/journals/category-static-journal/categories/second-old/"
        first = JournalCategoryPathRedirect.objects.create(
            category=self.category,
            journal=self.journal,
            old_path=first_old,
            new_path=second_old,
        )
        second = JournalCategoryPathRedirect.objects.create(
            category=self.category,
            journal=self.journal,
            old_path=second_old,
            new_path=canonical,
        )
        targets = [
            PublishTarget(
                canonical,
                f"journals.JournalCategory:{self.category.pk}:page:1",
                target_type="category_page",
                target_id=f"category:{self.category.pk}:page:1",
                canonical_path=canonical,
            ),
            PublishTarget(
                first.old_path,
                f"journals.JournalCategoryPathRedirect:{first.pk}",
                target_type="category_redirect",
                target_id=f"category_redirect:{first.pk}",
                canonical_path=first.new_path,
                action="redirect",
                http_status=301,
                redirect_to=first.new_path,
            ),
            PublishTarget(
                second.old_path,
                f"journals.JournalCategoryPathRedirect:{second.pk}",
                target_type="category_redirect",
                target_id=f"category_redirect:{second.pk}",
                canonical_path=second.new_path,
                action="redirect",
                http_status=301,
                redirect_to=second.new_path,
            ),
        ]
        with TemporaryDirectory() as staging:
            for target in targets:
                output = Path(staging, target.output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(target.render())
            with self.assertRaises(CategoryPublicationConsistencyError) as caught:
                validate_category_publication_consistency(
                    version_id="chain-test", targets=targets, staging=staging
                )
        self.assertIn(
            "CATEGORY_REDIRECT_CHAIN",
            {item["code"] for item in caught.exception.errors},
        )

    def test_publication_gate_blocks_output_path_conflicts(self):
        canonical = self.category.get_absolute_url()
        targets = [
            PublishTarget(
                canonical,
                f"journals.JournalCategory:{self.category.pk}:page:1",
                target_type="category_page",
                target_id=f"category:{self.category.pk}:page:1",
                canonical_path=canonical,
            ),
            PublishTarget("/duplicate/", "first", target_id="duplicate:first"),
            PublishTarget("/duplicate/", "second", target_id="duplicate:second"),
        ]
        with TemporaryDirectory() as staging:
            category_output = Path(staging, self.category.get_static_output_path())
            category_output.parent.mkdir(parents=True, exist_ok=True)
            category_output.write_text("category", encoding="utf-8")
            with self.assertRaises(CategoryPublicationConsistencyError) as caught:
                validate_category_publication_consistency(
                    version_id="conflict-test", targets=targets, staging=staging
                )
        self.assertIn(
            "STATIC_OUTPUT_PATH_CONFLICT",
            {item["code"] for item in caught.exception.errors},
        )

    def test_manifest_records_category_dependencies_and_static_redirect(self):
        article = self.create_live_article("Manifest Category Article")
        redirect = JournalCategoryPathRedirect.objects.create(
            category=self.category,
            journal=self.journal,
            old_path=("/journals/category-static-journal/categories/legacy-research/"),
            new_path=self.category.get_absolute_url(),
        )
        with (
            TemporaryDirectory() as output_root,
            self.settings(STATIC_PUBLISH_ROOT=output_root),
        ):
            publisher = StaticPublisher(output_root)
            job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
            )
            manifest = publisher.build(job)
            data = json.loads(
                Path(output_root, "current", "manifest.json").read_text(
                    encoding="utf-8"
                )
            )

            page = next(
                item
                for item in data["targets"]
                if item["target_id"] == f"category:{self.category.pk}:page:1"
            )
            redirect_item = next(
                item
                for item in data["targets"]
                if item["target_id"] == f"category_redirect:{redirect.pk}"
            )
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(page["canonical_path"], self.category.get_absolute_url())
            self.assertEqual(page["action"], "upsert")
            self.assertTrue(page["content_hash"].startswith("sha256:"))
            self.assertIn(article.pk, page["dependencies"]["article_ids"])
            self.assertTrue(page["dependencies"]["placement_ids"])
            self.assertEqual(redirect_item["action"], "redirect")
            self.assertEqual(redirect_item["http_status"], 301)
            self.assertEqual(
                redirect_item["redirect_to"], self.category.get_absolute_url()
            )
            redirect_html = Path(output_root, "current", redirect_item["output_path"])
            self.assertIn(
                "Moved permanently", redirect_html.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest.metadata["targets"], data["targets"])

    def test_selective_publish_can_remove_disabled_category_output(self):
        with (
            TemporaryDirectory() as output_root,
            self.settings(STATIC_PUBLISH_ROOT=output_root),
        ):
            publisher = StaticPublisher(output_root)
            first_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
            )
            publisher.build(first_job)
            output = Path(
                output_root, "current", self.category.get_static_output_path()
            )
            self.assertTrue(output.is_file())

            JournalCategory.objects.filter(pk=self.category.pk).update(
                status="disabled",
                generate_static_page=False,
                show_in_navigation=False,
            )
            delete_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.SELECTIVE,
                requested_paths=[self.category.get_absolute_url()],
                triggered_by=self.admin,
            )
            publisher.build(delete_job)
            delete_records = list(delete_job.targets.order_by("target_id"))
            self.assertEqual(len(delete_records), 2)
            self.assertTrue(all(record.action == "delete" for record in delete_records))
            self.assertFalse(output.exists())
            self.assertFalse(
                (
                    Path(output_root, "current", "en")
                    / self.category.get_static_output_path()
                ).exists()
            )
            active = StaticManifest.objects.get(is_active=True)
            deleted = next(
                item
                for item in active.metadata["targets"]
                if item["output_path"] == self.category.get_static_output_path()
            )
            self.assertEqual(deleted["action"], "delete")

    def test_failed_placement_sync_blocks_activation_and_preserves_release(self):
        article = self.create_live_article("Drift Category Article")
        with (
            TemporaryDirectory() as output_root,
            self.settings(STATIC_PUBLISH_ROOT=output_root),
        ):
            publisher = StaticPublisher(output_root)
            first_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
            )
            publisher.build(first_job)
            active_version = StaticManifest.objects.get(is_active=True).version
            active_manifest = Path(output_root, "current", "manifest.json").read_bytes()

            ArticlePage.objects.filter(pk=article.pk).update(
                placement_sync_status=ArticlePage.PlacementSyncStatus.FAILED,
                placement_sync_error="simulated synchronization failure",
            )
            failed_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
            )
            with self.assertRaisesMessage(PublishError, "CATEGORY_PUBLICATION_DRIFT"):
                publisher.build(failed_job)

            self.assertEqual(
                StaticManifest.objects.get(is_active=True).version,
                active_version,
            )
            self.assertEqual(
                Path(output_root, "current", "manifest.json").read_bytes(),
                active_manifest,
            )

    def test_rollback_restores_category_page_pagination_and_redirect_set(self):
        self.create_live_article("Rollback Category One")
        self.create_live_article("Rollback Category Two")
        with (
            TemporaryDirectory() as output_root,
            self.settings(STATIC_PUBLISH_ROOT=output_root),
        ):
            publisher = StaticPublisher(output_root)
            first_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
            )
            publisher.build(first_job)
            old_page = Path(
                output_root,
                "current",
                self.category.get_static_output_path(),
            )
            old_page_two = old_page.parent / "page" / "2" / "index.html"
            self.assertTrue(old_page.is_file())
            self.assertTrue(old_page_two.is_file())

            self.category.slug = "new-research"
            self.category.path_cache = "new-research"
            self.category.save(update_fields=("slug", "path_cache"))
            redirect = JournalCategoryPathRedirect.objects.create(
                category=self.category,
                journal=self.journal,
                old_path=("/journals/category-static-journal/categories/research/"),
                new_path=self.category.get_absolute_url(),
            )
            second_job = StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.FULL, triggered_by=self.admin
            )
            publisher.build(second_job)
            redirect_path = Path(
                output_root,
                "current",
                redirect.old_path.strip("/"),
                "index.html",
            )
            self.assertTrue(redirect_path.is_file())
            self.assertIn(
                "Moved permanently", redirect_path.read_text(encoding="utf-8")
            )

            publisher.rollback(
                first_job.version, self.admin, reason="rollback regression fixture"
            )
            self.assertTrue(old_page.is_file())
            self.assertTrue(old_page_two.is_file())
            self.assertNotIn("Moved permanently", old_page.read_text(encoding="utf-8"))
