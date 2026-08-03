from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from wagtail.models import Page

from ai_author_forum.articles.category_services import (
    ArticleCategoryError,
    get_live_article_categories,
    validate_article_category_revision,
)
from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.articles.services import sync_imported_article
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    StaticArticle,
    StaticArticleCategoryAssignment,
)
from ai_author_forum.placements.category_services import (
    disable_category_placements,
    sync_category_placements,
    validate_category_placement_consistency,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import AuditLog, AuditStatus


class CategoryRevisionWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="category-admin", email="category@example.com", password="test"
        )
        self.journal = Journal.objects.create(
            name="AI Journal", slug="ai-journal", az_group="A"
        )
        self.primary = JournalCategory.objects.create(
            journal=self.journal,
            name="Research",
            code="RESEARCH",
            slug="research",
            depth=1,
            path_cache="research",
        )
        self.related = JournalCategory.objects.create(
            journal=self.journal,
            name="Ethics",
            code="ETHICS",
            slug="ethics",
            depth=1,
            path_cache="ethics",
        )

    def create_article(self, *, title="Revision article", category=None):
        page = ArticlePage(
            title=title,
            slug=title.lower().replace(" ", "-"),
            abstract="Abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=self.journal,
            owner=self.user,
            review_status=ArticlePage.ReviewStatus.APPROVED,
        )
        Page.get_first_root_node().add_child(instance=page)
        if category is not None:
            ArticleCategoryAssignment.objects.create(
                article=page, category=category, is_primary=True
            )
        return page

    def publish_revision(self, page):
        revision = page.save_revision(
            user=self.user, bypass_article_permission_check=True
        )
        revision.publish(user=self.user, skip_permission_checks=True)
        ArticlePage.objects.filter(pk=page.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED
        )
        page.refresh_from_db()
        return revision

    def test_draft_can_be_saved_without_categories_but_submit_is_blocked(self):
        page = self.create_article(category=None)
        revision = page.save_revision(
            user=self.user, bypass_article_permission_check=True
        )
        self.assertIsNotNone(revision.pk)
        with self.assertRaises(ArticleCategoryError) as caught:
            validate_article_category_revision(article=page, action="submit")
        self.assertEqual(caught.exception.code, "ARTICLE_PRIMARY_CATEGORY_REQUIRED")

    def test_static_article_conversion_is_draft_atomic_and_revisioned(self):
        source = StaticArticle.objects.create(
            journal=self.journal,
            title="Imported article",
            slug="imported-article",
            authors="Author",
            abstract="Abstract",
            keywords="AI",
            review_status="published",
        )
        StaticArticleCategoryAssignment.objects.create(
            article=source, category=self.primary, is_primary=True, sort_order=0
        )
        page = sync_imported_article(source, owner=self.user)
        page.refresh_from_db()
        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertFalse(page.live)
        self.assertEqual(page.category_assignments.get().category, self.primary)
        revision_object = page.get_latest_revision().as_object()
        self.assertEqual(
            revision_object.category_assignments.get().category, self.primary
        )
        self.assertFalse(
            ArticlePlacement.objects.filter(article=page, source="system").exists()
        )

        same_page = sync_imported_article(source, owner=self.user)
        self.assertEqual(same_page.pk, page.pk)
        self.assertEqual(
            ArticlePage.objects.filter(source_static_article=source).count(), 1
        )

    def test_rejection_does_not_change_live_assignments_or_placements(self):
        page = self.create_article(category=self.primary)
        live_revision = self.publish_revision(page)
        sync_category_placements(
            article_id=page.pk, revision_id=live_revision.pk, actor=self.user
        )

        ArticleCategoryAssignment.objects.filter(article=page).delete()
        ArticleCategoryAssignment.objects.create(
            article=page, category=self.related, is_primary=True
        )
        page.save_revision(user=self.user, bypass_article_permission_check=True)
        page.reject(self.user, "Needs changes")

        live_categories = get_live_article_categories(article_id=page.pk)
        active_ids = set(
            ArticlePlacement.objects.filter(
                article=page, source="system", is_active=True
            ).values_list("target_category_id", flat=True)
        )
        self.assertEqual(live_categories.primary, self.primary)
        self.assertEqual(active_ids, {self.primary.pk})

    def test_business_approval_retries_category_sync_for_live_article(self):
        page = self.create_article(category=self.primary)
        revision = self.publish_revision(page)
        page.submit_for_review(self.user, "Ready for review")

        with self.captureOnCommitCallbacks(execute=True):
            page.approve(self.user, "Approved")

        placement = ArticlePlacement.objects.get(
            article=page,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category=self.primary,
            source=ArticlePlacement.Source.SYSTEM,
            placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
        )
        page.refresh_from_db()
        self.assertTrue(placement.is_active)
        self.assertEqual(placement.metadata["category_assignment_role"], "primary")
        self.assertEqual(page.placement_sync_status, "synced")
        self.assertEqual(page.placement_synced_revision_id, revision.pk)

    def test_sync_without_revision_reads_live_revision_not_unpublished_draft(self):
        page = self.create_article(category=self.primary)
        live_revision = self.publish_revision(page)
        sync_category_placements(
            article_id=page.pk, revision_id=live_revision.pk, actor=self.user
        )

        ArticleCategoryAssignment.objects.filter(article=page).delete()
        ArticleCategoryAssignment.objects.create(
            article=page, category=self.related, is_primary=True
        )
        page.save_revision(user=self.user, bypass_article_permission_check=True)

        sync_category_placements(article_id=page.pk, actor=self.user)
        active = ArticlePlacement.objects.filter(
            article=page, source="system", is_active=True
        )
        self.assertEqual(
            list(active.values_list("target_category_id", flat=True)),
            [self.primary.pk],
        )
        self.assertEqual(
            get_live_article_categories(article_id=page.pk).primary, self.primary
        )

    def test_publish_rollback_and_repeated_sync_are_idempotent(self):
        page = self.create_article(category=self.primary)
        first_revision = self.publish_revision(page)
        first = sync_category_placements(
            article_id=page.pk, revision_id=first_revision.pk, actor=self.user
        )
        repeated = sync_category_placements(
            article_id=page.pk, revision_id=first_revision.pk, actor=self.user
        )
        self.assertEqual(len(first["created"]), 1)
        self.assertFalse(first["idempotent"])
        placement = ArticlePlacement.objects.get(pk=first["created"][0])
        first_metadata = dict(placement.metadata)
        first_audit_count = AuditLog.objects.filter(
            status=AuditStatus.SUCCESS,
            metadata__operation="sync_category_placements",
            target_id=str(page.pk),
        ).count()
        self.assertEqual(repeated["created"], [])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["request_id"], first["request_id"])
        page.refresh_from_db()
        placement.refresh_from_db()
        self.assertEqual(page.placement_sync_request_id, first["request_id"])
        self.assertEqual(placement.metadata, first_metadata)
        self.assertEqual(
            AuditLog.objects.filter(
                status=AuditStatus.SUCCESS,
                metadata__operation="sync_category_placements",
                target_id=str(page.pk),
            ).count(),
            first_audit_count,
        )

        ArticleCategoryAssignment.objects.filter(article=page).delete()
        ArticleCategoryAssignment.objects.create(
            article=page, category=self.related, is_primary=True
        )
        second_revision = self.publish_revision(page)
        sync_category_placements(
            article_id=page.pk, revision_id=second_revision.pk, actor=self.user
        )
        self.assertEqual(
            set(
                ArticlePlacement.objects.filter(
                    article=page, source="system", is_active=True
                ).values_list("target_category_id", flat=True)
            ),
            {self.related.pk},
        )

        first_revision.publish(user=self.user, skip_permission_checks=True)
        ArticlePage.objects.filter(pk=page.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED
        )
        page.refresh_from_db()
        sync_category_placements(
            article_id=page.pk, revision_id=first_revision.pk, actor=self.user
        )
        self.assertEqual(
            set(
                ArticlePlacement.objects.filter(
                    article=page, source="system", is_active=True
                ).values_list("target_category_id", flat=True)
            ),
            {self.primary.pk},
        )
        self.assertFalse(validate_category_placement_consistency(article_ids=[page.pk]))

    def test_unpublish_disables_system_placements_without_touching_manual(self):
        page = self.create_article(category=self.primary)
        revision = self.publish_revision(page)
        sync_category_placements(
            article_id=page.pk, revision_id=revision.pk, actor=self.user
        )
        manual_slot = LayoutSlot.objects.create(
            title="Category feature",
            code="category-feature",
            scope=LayoutSlot.Scope.CATEGORY,
            max_items=3,
        )
        manual = ArticlePlacement.objects.create(
            slot=manual_slot,
            article=page,
            target_type=ArticlePlacement.TargetType.CATEGORY,
            target_category=self.primary,
            placement_kind=ArticlePlacement.PlacementKind.FEATURED,
            source=ArticlePlacement.Source.MANUAL,
            is_active=True,
        )

        page.unpublish(user=self.user)
        disable_category_placements(article_id=page.pk, actor=self.user)

        self.assertFalse(
            ArticlePlacement.objects.get(
                article=page, source=ArticlePlacement.Source.SYSTEM
            ).is_active
        )
        manual.refresh_from_db()
        self.assertTrue(manual.is_active)

    def test_sync_failure_is_visible_audited_and_retryable(self):
        page = self.create_article(category=self.primary)
        revision = self.publish_revision(page)
        with patch(
            "ai_author_forum.placements.category_services._sync_category_placements_atomic",
            side_effect=RuntimeError("simulated sync failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated sync failure"):
                sync_category_placements(
                    article_id=page.pk, revision_id=revision.pk, actor=self.user
                )

        page.refresh_from_db()
        self.assertEqual(
            page.placement_sync_status, ArticlePage.PlacementSyncStatus.FAILED
        )
        self.assertIn("simulated sync failure", page.placement_sync_error)
        failure = AuditLog.objects.filter(
            status=AuditStatus.FAILURE,
            metadata__operation="sync_category_placements",
            target_id=str(page.pk),
        ).latest("pk")
        self.assertEqual(failure.metadata["expected_category_ids"], [self.primary.pk])

        sync_category_placements(
            article_id=page.pk, revision_id=revision.pk, actor=self.user
        )
        page.refresh_from_db()
        self.assertEqual(
            page.placement_sync_status, ArticlePage.PlacementSyncStatus.SYNCED
        )
        self.assertEqual(page.placement_sync_error, "")
