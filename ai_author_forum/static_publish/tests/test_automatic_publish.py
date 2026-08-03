from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.home.models import HomePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.news.models import NewsListingPage
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.placements.services import save_manual_placement
from ai_author_forum.static_publish.automatic import (
    _queue_placement_publish,
    create_pending_placement_publish,
    queue_placement_publish,
)
from ai_author_forum.static_publish.models import (
    StaticManifest,
    StaticPublishJob,
    StaticPublishTarget,
)
from ai_author_forum.static_publish.services import PublishLocked
from ai_author_forum.static_publish.tasks import run_coalesced_static_publish


@override_settings(
    STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE=True,
    STATIC_PUBLISH_AUTO_DEBOUNCE_SECONDS=60,
)
class AutomaticPlacementPublishTests(TestCase):
    @patch(
        "ai_author_forum.static_publish.tasks.run_coalesced_static_publish.apply_async"
    )
    def test_pending_placement_batches_merge_paths_and_changes(self, enqueue):
        first = _queue_placement_publish(
            [
                {
                    "placement_id": 101,
                    "article_static_slug": "first-article",
                    "target_type": "main_site",
                }
            ],
            actor_id=None,
            reason="placement_change",
        )
        second = _queue_placement_publish(
            [
                {
                    "placement_id": 102,
                    "article_static_slug": "second-article",
                    "target_type": "journal",
                    "target_slug": "nature-ai",
                }
            ],
            actor_id=None,
            reason="placement_change",
        )

        self.assertEqual(first.pk, second.pk)
        job = StaticPublishJob.objects.get(pk=first.pk)
        self.assertTrue(job.is_automatic)
        self.assertEqual(job.scope, StaticPublishJob.Scope.SELECTIVE)
        self.assertCountEqual(
            job.requested_paths,
            [
                "/",
                "/articles/first-article/",
                "/articles/second-article/",
                "/journals/nature-ai/",
                "/search/",
            ],
        )
        self.assertEqual(job.summary["change_count"], 2)
        self.assertEqual(job.summary["placement_ids"], [101, 102])
        self.assertEqual(enqueue.call_count, 2)

    @patch(
        "ai_author_forum.static_publish.tasks.run_coalesced_static_publish.apply_async"
    )
    def test_manual_placement_save_queues_an_automatic_incremental_job(self, enqueue):
        user = get_user_model().objects.create_superuser(
            username="automatic-publisher",
            email="automatic@example.com",
            password="test",
        )
        home = HomePage.objects.first()
        listing = NewsListingPage(title="News", slug="automatic-news")
        home.add_child(instance=listing)
        listing.save_revision().publish()
        journal = Journal.objects.create(
            name="Nature AI",
            name_cn="Nature AI",
            slug="nature-ai",
            az_group="N",
            status="active",
        )
        article = ArticlePage(
            title="Automatic article",
            slug="automatic-article",
            static_slug="automatic-article",
            abstract="Automatic article abstract.",
            body=[{"type": "paragraph", "value": "Automatic article body."}],
            authors="Editor",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            review_status=ArticlePage.ReviewStatus.APPROVED,
            primary_journal=journal,
            keywords="ai",
        )
        listing.add_child(instance=article)
        article.save_revision().publish()
        slot = LayoutSlot.objects.get(code="home_featured")

        with self.captureOnCommitCallbacks(execute=True):
            save_manual_placement(
                ArticlePlacement(slot=slot, article=article), actor=user
            )

        job = StaticPublishJob.objects.get(is_automatic=True)
        self.assertCountEqual(
            job.requested_paths,
            ["/", "/articles/automatic-article/", "/search/"],
        )
        enqueue.assert_called_once_with(args=(job.pk,), eta=job.scheduled_at)

    @patch(
        "ai_author_forum.static_publish.tasks.run_coalesced_static_publish.apply_async"
    )
    @patch("ai_author_forum.static_publish.automatic.import_string")
    def test_category_change_includes_current_and_stale_pagination_paths(
        self, import_target_provider, enqueue
    ):
        current_target = SimpleNamespace(
            url="/journals/nature-ai/topics/machine-learning/",
            target_type="category_page",
            dependencies={"category_ids": [42]},
        )
        import_target_provider.return_value.return_value.get_targets.return_value = [
            current_target
        ]
        previous_job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            status=StaticPublishJob.Status.SUCCEEDED,
        )
        StaticManifest.objects.create(
            version="previous-category-release",
            job=previous_job,
            files=[],
            is_active=True,
        )
        StaticPublishTarget.objects.create(
            job=previous_job,
            path="journals/nature-ai/topics/machine-learning/page/3/index.html",
            target_type="category_page",
            dependencies={"category_ids": [42]},
        )

        job = _queue_placement_publish(
            [
                {
                    "placement_id": 101,
                    "article_static_slug": "first-article",
                    "target_type": "category",
                    "target_category_id": 42,
                }
            ],
            actor_id=None,
            reason="category_placement_sync",
        )

        self.assertCountEqual(
            job.requested_paths,
            [
                "/articles/first-article/",
                "/journals/nature-ai/topics/machine-learning/",
                "/journals/nature-ai/topics/machine-learning/page/3/index.html",
                "/search/",
            ],
        )
        enqueue.assert_called_once_with(args=(job.pk,), eta=job.scheduled_at)

    @override_settings(STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE=False)
    @patch(
        "ai_author_forum.static_publish.tasks.run_coalesced_static_publish.apply_async"
    )
    def test_feature_flag_keeps_manual_control_when_disabled(self, enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            queue_placement_publish(
                {
                    "placement_id": 101,
                    "article_static_slug": "first-article",
                    "target_type": "main_site",
                }
            )

        self.assertFalse(StaticPublishJob.objects.filter(is_automatic=True).exists())
        enqueue.assert_not_called()

    @patch("ai_author_forum.static_publish.tasks.StaticPublisher")
    @patch(
        "ai_author_forum.static_publish.tasks.run_coalesced_static_publish.apply_async"
    )
    def test_task_defers_an_early_message_until_latest_merge_deadline(
        self, enqueue, publisher_class
    ):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.SELECTIVE,
            requested_paths=["/"],
            is_automatic=True,
            coalesce_key="placement-change",
            scheduled_at=timezone.now() + timedelta(seconds=60),
        )

        result = run_coalesced_static_publish.run(job.pk)

        self.assertEqual(result["job_id"], job.pk)
        self.assertIn("deferred_seconds", result)
        enqueue.assert_called_once_with(args=(job.pk,), eta=job.scheduled_at)
        publisher_class.return_value.build.assert_not_called()

    @patch(
        "ai_author_forum.static_publish.tasks.StaticPublisher.build",
        side_effect=PublishLocked("A static build is already running"),
    )
    def test_preflight_failure_does_not_leave_claimed_job_running(self, _build):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.SELECTIVE,
            requested_paths=["/"],
            is_automatic=True,
            coalesce_key="placement-change",
            scheduled_at=timezone.now() - timedelta(seconds=1),
        )

        with self.assertRaisesMessage(PublishLocked, "already running"):
            run_coalesced_static_publish.run(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, StaticPublishJob.Status.FAILED)
        self.assertIn("already running", job.error)

    @patch("ai_author_forum.static_publish.tasks.StaticPublisher")
    def test_duplicate_worker_messages_build_an_automatic_job_once(
        self, publisher_class
    ):
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.SELECTIVE,
            requested_paths=["/"],
            is_automatic=True,
            coalesce_key="placement-change",
            scheduled_at=timezone.now() - timedelta(seconds=1),
        )

        run_coalesced_static_publish.run(job.pk)
        second = run_coalesced_static_publish.run(job.pk)

        publisher_class.return_value.build.assert_called_once()
        self.assertTrue(second["skipped"])

    def test_replacing_hero_or_visual_story_always_requests_homepage_rebuild(self):
        slot_codes = ("home_hero", "home_visual_stories")

        for index, slot_code in enumerate(slot_codes, start=1):
            with self.subTest(slot=slot_code):
                job = create_pending_placement_publish(
                    {
                        "placement_id": index,
                        "article_static_slug": f"old-{index}",
                        "slot": slot_code,
                        "target_type": ArticlePlacement.TargetType.MAIN_SITE,
                        "target_slug": "",
                        "is_active": True,
                    },
                    {
                        "placement_id": index,
                        "article_static_slug": f"new-{index}",
                        "slot": slot_code,
                        "target_type": ArticlePlacement.TargetType.MAIN_SITE,
                        "target_slug": "",
                        "is_active": True,
                    },
                )

                self.assertIn("/", job.requested_paths)
                self.assertIn(f"/articles/old-{index}/", job.requested_paths)
                self.assertIn(f"/articles/new-{index}/", job.requested_paths)
                self.assertTrue(job.summary["requires_publisher_approval"])

    @patch("ai_author_forum.placements.services.queue_placement_publish")
    def test_content_admin_save_creates_pending_publisher_job_without_queueing(
        self, queue
    ):
        user = get_user_model().objects.create_user(
            username="content-placement-admin",
            email="content-placement@example.com",
            password="test",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="placements",
                codename="manage_manual_categoryplacement",
            ),
            Permission.objects.get(
                content_type__app_label="placements",
                codename="add_articleplacement",
            ),
        )
        home = HomePage.objects.first()
        listing = NewsListingPage(title="Pending News", slug="pending-news")
        home.add_child(instance=listing)
        listing.save_revision().publish()
        journal = Journal.objects.create(
            name="Pending Journal",
            slug="pending-journal",
            az_group="P",
            status="active",
        )
        article = ArticlePage(
            title="Pending placement article",
            slug="pending-placement-article",
            static_slug="pending-placement-article",
            abstract="Pending placement abstract.",
            body=[{"type": "paragraph", "value": "Pending placement body."}],
            authors="Editor",
            article_type=ArticlePage.ArticleType.AI_ARTICLE,
            review_status=ArticlePage.ReviewStatus.APPROVED,
            primary_journal=journal,
            keywords="ai",
        )
        listing.add_child(instance=article)
        article.save_revision().publish()

        placement = save_manual_placement(
            ArticlePlacement(
                slot=LayoutSlot.objects.get(code="home_featured"),
                article=article,
            ),
            actor=user,
        )

        queue.assert_not_called()
        job = placement.pending_publish_job
        self.assertEqual(job.status, StaticPublishJob.Status.PENDING)
        self.assertFalse(job.is_automatic)
        self.assertEqual(job.triggered_by, user)
        self.assertTrue(job.summary["requires_publisher_approval"])
        self.assertEqual(job.summary["placement_ids"], [placement.pk])
        self.assertCountEqual(
            job.requested_paths,
            ["/", "/articles/pending-placement-article/", "/search/"],
        )
