from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from wagtail.models import Page

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.articles.review_services import (
    final_review_article,
    initial_review_article,
    submit_article_for_initial_review,
)
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.access_control import filter_accessible_placements
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.static_publish.services import require_publish_job_permission
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

from ..batch_operations import (
    create_maintenance_batch,
    execute_maintenance_batch,
    precheck_maintenance_batch,
)
from ..batch_services import create_draft, execute_create_batch, update_draft
from ..models import ArticlePlacement, LayoutSlot, PlacementBatch
from ..publishing import supersede_pending_chief_publish_jobs
from ..services import save_manual_placement


class SimpleRbacPlacementAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        user_model = get_user_model()
        cls.admin = user_model.objects.create_user(
            username="placement-platform-admin",
            email="placement-platform-admin@example.com",
            display_name="Placement Platform Admin",
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.chief = cls.make_user("placement-chief")
        cls.executive = cls.make_user("placement-executive")
        cls.associate = cls.make_user("placement-associate")
        cls.other_chief = cls.make_user("placement-other-chief")
        cls.journal = Journal.objects.create(
            name="Placement Journal",
            slug="placement-rbac-journal",
            status=JournalStatus.ACTIVE,
            az_group="P",
        )
        cls.other_journal = Journal.objects.create(
            name="Other Placement Journal",
            slug="placement-rbac-other-journal",
            status=JournalStatus.ACTIVE,
            az_group="P",
        )
        cls.category = JournalCategory.objects.create(
            journal=cls.journal,
            name="Placement category",
            code="placement-category",
            slug="placement-category",
        )
        cls.other_category = JournalCategory.objects.create(
            journal=cls.other_journal,
            name="Other placement category",
            code="other-placement-category",
            slug="other-placement-category",
        )
        cls.appoint(cls.chief, cls.journal, JournalEditorAssignment.Role.CHIEF_EDITOR)
        cls.appoint(
            cls.executive,
            cls.journal,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        )
        cls.appoint(
            cls.associate,
            cls.journal,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE],
        )
        cls.appoint(
            cls.other_chief,
            cls.other_journal,
            JournalEditorAssignment.Role.CHIEF_EDITOR,
        )
        cls.article = cls.create_article(
            "Placement approved article",
            cls.journal,
            cls.category,
            cls.chief,
            cls.executive,
            approve=True,
        )
        cls.target_article = cls.create_article(
            "Placement target article",
            cls.journal,
            cls.category,
            cls.chief,
            cls.executive,
            approve=True,
        )
        cls.other_target_article = cls.create_article(
            "Other placement target article",
            cls.other_journal,
            cls.other_category,
            cls.other_chief,
            cls.other_chief,
            approve=True,
        )
        cls.draft_article = cls.create_article(
            "Placement draft article",
            cls.journal,
            cls.category,
            cls.chief,
            cls.executive,
            approve=False,
        )
        cls.article.related_journals.add(cls.other_journal)
        cls.category_slot = LayoutSlot.objects.create(
            title="RBAC category slot",
            code="rbac_category_slot",
            scope=LayoutSlot.Scope.CATEGORY,
            max_items=20,
        )
        cls.article_slot = LayoutSlot.objects.create(
            title="RBAC article slot",
            code="rbac_article_slot",
            scope=LayoutSlot.Scope.ARTICLE,
            max_items=20,
        )

    @classmethod
    def make_user(cls, username):
        return get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username.replace("-", " ").title(),
            is_staff=True,
        )

    @classmethod
    def appoint(cls, user, journal, role, responsibilities=()):
        return appoint_journal_editor(
            actor=cls.admin,
            user=user,
            journal=journal,
            role=role,
            responsibilities=responsibilities,
            public_profile={
                "public_name": user.display_name,
                "public_role_label": (
                    JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
                ),
                "show_publicly": True,
                "display_order": 1,
            },
        )

    @classmethod
    def create_article(
        cls,
        title,
        journal,
        category,
        chief,
        initial_editor,
        *,
        approve,
    ):
        slug = title.lower().replace(" ", "-")
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract=f"{title} abstract",
            body=[("paragraph", f"<p>{title} body</p>")],
            authors="Placement Author",
            keywords="placement",
            responsibility_statement="Authors retain responsibility.",
            article_type=ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            primary_journal=journal,
        )
        Page.get_first_root_node().add_child(instance=article)
        ArticleCategoryAssignment.objects.create(
            article=article,
            category=category,
            is_primary=True,
        )
        revision = article.save_revision(
            user=chief,
            bypass_article_permission_check=True,
        )
        if not approve:
            return article
        submit_article_for_initial_review(
            actor=chief,
            article=article,
            expected_state=ArticlePage.ReviewStatus.DRAFT,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
            comment="Placement acceptance submission.",
        )
        article.refresh_from_db()
        initial_review_article(
            actor=initial_editor,
            article=article,
            action="approve",
            comment="Placement acceptance initial approval.",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        final_review_article(
            actor=chief,
            article=article,
            action="approve",
            comment="Placement acceptance final approval.",
            expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        return article

    def placement(
        self, *, target_type, slot, target_slug="", category=None, article=None
    ):
        return ArticlePlacement(
            article=article or self.article,
            slot=slot,
            target_type=target_type,
            target_slug=target_slug,
            target_category=category,
        )

    def test_unreviewed_or_revision_mismatched_article_cannot_be_placed(self):
        placement = self.placement(
            article=self.draft_article,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
            slot=LayoutSlot.objects.get(code="journal_featured"),
        )
        with self.assertRaises(ValidationError):
            save_manual_placement(placement, actor=self.chief)

        article = self.article
        mismatched_revision = article.save_revision(
            user=self.chief,
            changed=False,
            bypass_article_permission_check=True,
        )
        ArticlePage.objects.filter(pk=article.pk).update(
            approved_version=mismatched_revision,
            review_status=ArticlePage.ReviewStatus.APPROVED,
        )
        article.refresh_from_db()
        placement = self.placement(
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
            slot=LayoutSlot.objects.get(code="journal_featured"),
        )
        with self.assertRaises(ValidationError):
            save_manual_placement(placement, actor=self.chief)

    def test_only_chief_can_create_same_journal_targets(self):
        cases = (
            (
                self.chief,
                ArticlePlacement.TargetType.JOURNAL,
                LayoutSlot.objects.get(code="journal_featured"),
                self.journal.slug,
                None,
            ),
            (
                self.chief,
                ArticlePlacement.TargetType.CATEGORY,
                self.category_slot,
                "",
                self.category,
            ),
            (
                self.chief,
                ArticlePlacement.TargetType.ARTICLE,
                self.article_slot,
                self.target_article.static_slug,
                None,
            ),
        )
        for actor, target_type, slot, target_slug, category in cases:
            with self.subTest(target_type=target_type, actor=actor.username):
                placement = self.placement(
                    target_type=target_type,
                    slot=slot,
                    target_slug=target_slug,
                    category=category,
                )
                self.assertIsNotNone(save_manual_placement(placement, actor=actor).pk)

        deputy_placement = self.placement(
            target_type=ArticlePlacement.TargetType.JOURNAL,
            slot=LayoutSlot.objects.get(code="journal_latest"),
            target_slug=self.journal.slug,
        )
        with self.assertRaises(PermissionDenied):
            save_manual_placement(deputy_placement, actor=self.executive)

    def test_journal_editors_cannot_manage_global_search_or_cross_journal_targets(self):
        cases = (
            (
                ArticlePlacement.TargetType.MAIN_SITE,
                LayoutSlot.objects.get(code="home_featured"),
                "",
                None,
            ),
            (
                ArticlePlacement.TargetType.SEARCH,
                LayoutSlot.objects.get(code="search_recommended"),
                "search",
                None,
            ),
            (
                ArticlePlacement.TargetType.JOURNAL,
                LayoutSlot.objects.get(code="journal_latest"),
                self.other_journal.slug,
                None,
            ),
            (
                ArticlePlacement.TargetType.CATEGORY,
                self.category_slot,
                "",
                self.other_category,
            ),
            (
                ArticlePlacement.TargetType.ARTICLE,
                self.article_slot,
                self.other_target_article.static_slug,
                None,
            ),
        )
        for target_type, slot, target_slug, category in cases:
            with self.subTest(target_type=target_type, target_slug=target_slug):
                placement = self.placement(
                    target_type=target_type,
                    slot=slot,
                    target_slug=target_slug,
                    category=category,
                )
                with self.assertRaises(PermissionDenied):
                    save_manual_placement(placement, actor=self.chief)

    def test_associate_cannot_create_placement_and_queryset_is_strictly_scoped(self):
        placement = self.placement(
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
            slot=LayoutSlot.objects.get(code="journal_hero"),
        )
        with self.assertRaises(PermissionDenied):
            save_manual_placement(placement, actor=self.associate)

        ArticlePlacement.objects.create(
            article=self.article,
            slot=LayoutSlot.objects.get(code="journal_highlights"),
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
        )
        ArticlePlacement.objects.create(
            article=self.article,
            slot=LayoutSlot.objects.get(code="home_featured"),
            target_type=ArticlePlacement.TargetType.MAIN_SITE,
        )
        visible = filter_accessible_placements(
            self.chief,
            ArticlePlacement.objects.all(),
        )
        self.assertTrue(visible.exists())
        self.assertFalse(
            visible.exclude(article__primary_journal=self.journal).exists()
        )
        self.assertFalse(
            visible.filter(
                target_type__in=(
                    ArticlePlacement.TargetType.MAIN_SITE,
                    ArticlePlacement.TargetType.SEARCH,
                )
            ).exists()
        )

    def test_batch_execution_rechecks_role_after_assignment_expires(self):
        batch = create_draft(
            actor=self.chief,
            mode=PlacementBatch.Mode.SINGLE,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
            slot=LayoutSlot.objects.get(code="journal_latest"),
        )
        update_draft(
            batch,
            actor=self.chief,
            selected_article_ids=[self.target_article.pk],
        )
        JournalEditorAssignment.objects.filter(
            user=self.chief,
            journal=self.journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
        ).update(is_active=False)

        with self.assertRaises(PermissionDenied):
            execute_create_batch(batch, actor=self.chief)
        self.assertFalse(
            ArticlePlacement.objects.filter(
                article=self.target_article,
                slot=batch.slot,
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=self.journal.slug,
            ).exists()
        )

    @patch("ai_author_forum.static_publish.tasks.run_static_publish.apply_async")
    def test_deputy_republishes_existing_article_but_not_unapproved_revision(
        self, enqueue
    ):
        placement = ArticlePlacement.objects.create(
            article=self.article,
            slot=LayoutSlot.objects.get(code="journal_latest"),
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
        )
        ArticlePage.objects.filter(pk=self.article.pk).update(
            last_static_published_at=timezone.now(),
            published_version="prior-release",
            publication_status=ArticlePage.PublicationStatus.PUBLISHED,
        )
        self.article.refresh_from_db()

        batch = create_maintenance_batch(
            actor=self.associate,
            operation=PlacementBatch.Operation.REPUBLISH,
            placement_ids=[placement.pk],
        )
        self.assertEqual(precheck_maintenance_batch(batch, actor=self.associate), [])
        with self.captureOnCommitCallbacks(execute=True):
            execute_maintenance_batch(batch, actor=self.associate)
        batch.refresh_from_db()
        self.assertEqual(batch.publish_status, PlacementBatch.PublishStatus.QUEUED)
        self.assertTrue(batch.publish_job.is_automatic)
        self.assertFalse(
            batch.publish_job.summary.get("requires_publisher_approval", True)
        )
        enqueue.assert_called_once_with(args=(batch.publish_job_id,))
        self.assertIsNone(require_publish_job_permission(batch.publish_job))
        batch.publish_job.requested_paths = [
            *(batch.publish_job.requested_paths or []),
            "/",
        ]
        with self.assertRaises(PermissionDenied):
            require_publish_job_permission(batch.publish_job)

        ArticlePage.objects.filter(pk=self.article.pk).update(
            review_status=ArticlePage.ReviewStatus.DRAFT,
            approved_version=None,
        )
        blocked = create_maintenance_batch(
            actor=self.executive,
            operation=PlacementBatch.Operation.REPUBLISH,
            placement_ids=[placement.pk],
        )
        self.assertTrue(precheck_maintenance_batch(blocked, actor=self.executive))

    @patch("ai_author_forum.static_publish.tasks.run_static_publish.apply_async")
    def test_pending_chief_jobs_are_merged_before_policy_transition_publish(
        self, enqueue
    ):
        placements = [
            ArticlePlacement.objects.create(
                article=article,
                slot=LayoutSlot.objects.get(code=slot_code),
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug=self.journal.slug,
            )
            for article, slot_code in (
                (self.article, "journal_featured"),
                (self.target_article, "journal_latest"),
            )
        ]
        pending = [
            StaticPublishJob.objects.create(
                scope=StaticPublishJob.Scope.SELECTIVE,
                requested_paths=[],
                triggered_by=self.chief,
                summary={
                    "requires_publisher_approval": True,
                    "placement_ids": [placement.pk],
                },
            )
            for placement in placements
        ]
        unrelated_automatic = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.SELECTIVE,
            is_automatic=True,
            triggered_by=self.admin,
            summary={"trigger": "unrelated_pending_automation"},
        )

        with self.captureOnCommitCallbacks(execute=True):
            replacement = supersede_pending_chief_publish_jobs(
                [job.pk for job in pending]
            )

        replacement.refresh_from_db()
        self.assertTrue(replacement.is_automatic)
        self.assertEqual(
            replacement.coalesce_key,
            f"chief-transition:{self.chief.pk}:{pending[0].pk}-{pending[-1].pk}",
        )
        self.assertEqual(
            replacement.summary["supersedes_pending_jobs"],
            [job.pk for job in pending],
        )
        self.assertIsNone(require_publish_job_permission(replacement))
        enqueue.assert_called_once_with(args=(replacement.pk,))
        self.assertEqual(
            set(
                StaticPublishJob.objects.filter(
                    pk__in=[job.pk for job in pending]
                ).values_list("status", flat=True)
            ),
            {StaticPublishJob.Status.FAILED},
        )
        unrelated_automatic.refresh_from_db()
        self.assertEqual(unrelated_automatic.status, StaticPublishJob.Status.PENDING)
