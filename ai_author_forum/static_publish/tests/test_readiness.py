from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image as PillowImage
from wagtail.models import Page

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.articles.review_services import (
    final_review_article,
    initial_review_article,
    submit_article_for_initial_review,
)
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.models import (
    IssueArticle,
    Journal,
    JournalCategory,
    JournalEditorAssignment,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import (
    ColumnEmptyBehavior,
    NavigationItem,
    NavigationTargetType,
)
from ai_author_forum.site_settings.navigation import ensure_main_navigation_set
from ai_author_forum.static_publish.providers import PublishTarget
from ai_author_forum.static_publish.readiness import (
    check_content_readiness,
    check_homepage_readiness,
)
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


def uploaded_image(name="readiness.png"):
    stream = BytesIO()
    PillowImage.new("RGB", (32, 24), "navy").save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class ContentReadinessTests(TestCase):
    def setUp(self):
        self.media_temporary = TemporaryDirectory()
        self.addCleanup(self.media_temporary.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_temporary.name)
        media_override.enable()
        self.addCleanup(media_override.disable)

        navigation_set = ensure_main_navigation_set()
        self.item = NavigationItem.objects.get(
            group__navigation_set=navigation_set,
            code="news",
        )
        NavigationItem.objects.exclude(pk=self.item.pk).update(is_active=False)
        self.config = self.item.content_column_config
        self.config.minimum_publish_items = 1
        self.config.empty_behavior = ColumnEmptyBehavior.BLOCK_PUBLISH
        self.config.cover_image = None
        self.config.save()
        self.slot = LayoutSlot.objects.get(code="column_list")
        self.journal = Journal.objects.create(
            name="Readiness Journal",
            slug="readiness-journal",
            az_group="R",
        )
        self.category = JournalCategory.objects.create(
            journal=self.journal,
            name="Readiness category",
            code="readiness-category",
            slug="readiness-category",
        )
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="readiness-admin",
            email="readiness-admin@example.com",
            display_name="Readiness Admin",
            is_staff=True,
        )
        self.admin.groups.add(
            Group.objects.get_or_create(name=SUPER_ADMIN_GROUP_NAME)[0]
        )
        self.chief = user_model.objects.create_user(
            username="readiness-review-chief",
            email="readiness-review-chief@example.com",
            display_name="Readiness Review Chief",
            is_staff=True,
        )
        self.executive = user_model.objects.create_user(
            username="readiness-review-executive",
            email="readiness-review-executive@example.com",
            display_name="Readiness Review Executive",
            is_staff=True,
        )
        for user, role in (
            (self.chief, JournalEditorAssignment.Role.CHIEF_EDITOR),
            (self.executive, JournalEditorAssignment.Role.EXECUTIVE_EDITOR),
        ):
            JournalEditorAssignment.objects.create(
                user=user,
                journal=self.journal,
                role=role,
                responsibilities=list(JournalEditorAssignment.ALL_RESPONSIBILITIES),
                public_name=user.display_name,
                public_role_label=(
                    JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
                ),
                created_by=self.admin,
            )

    def create_article(
        self,
        *,
        title="Readiness Article",
        review_status=ArticlePage.ReviewStatus.APPROVED,
        journal=None,
        image=None,
        image_alt="",
    ):
        slug = title.lower().replace(" ", "-")
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Readiness abstract",
            body=[("paragraph", "<p>Readiness body</p>")],
            authors="Readiness author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=journal or self.journal,
            keywords="readiness",
            featured_image=image,
            featured_image_alt=image_alt,
        )
        Page.get_first_root_node().add_child(instance=article)
        ArticleCategoryAssignment.objects.create(
            article=article,
            category=self.category,
            is_primary=True,
        )
        revision = article.save_revision(
            user=self.chief,
            bypass_article_permission_check=True,
        )
        if review_status == ArticlePage.ReviewStatus.APPROVED:
            submit_article_for_initial_review(
                actor=self.chief,
                article=article,
                expected_state=ArticlePage.ReviewStatus.DRAFT,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
                comment="Readiness fixture submission.",
            )
            article.refresh_from_db()
            initial_review_article(
                actor=self.executive,
                article=article,
                action="approve",
                comment="Readiness fixture initial approval.",
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
            article.refresh_from_db()
            final_review_article(
                actor=self.chief,
                article=article,
                action="approve",
                comment="Readiness fixture final approval.",
                expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
            article.refresh_from_db()
        return article

    def place(self, article):
        return ArticlePlacement.objects.create(
            article=article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug=self.item.placement_target_slug,
        )

    def finding_codes(self, result):
        return {finding.code for finding in result.blockers}

    def test_core_column_below_minimum_blocks_publish(self):
        result = check_content_readiness()

        self.assertIn("column_minimum_not_met", self.finding_codes(result))
        self.assertEqual(result.checked_columns, 1)
        self.assertEqual(result.checked_placements, 0)

    def test_active_journal_requires_exactly_one_effective_chief_editor(self):
        journal = Journal.objects.create(
            name="Readiness Journal Without Chief",
            slug="readiness-journal-without-chief",
            az_group="R",
        )
        result = check_content_readiness()
        invalid_journal_ids = {
            finding.target_id
            for finding in result.blockers
            if finding.code == "active_journal_chief_invalid"
        }
        self.assertIn(str(journal.pk), invalid_journal_ids)

        chief = get_user_model().objects.create_user(
            username="readiness-chief",
            email="readiness-chief@example.com",
            display_name="Readiness Chief",
        )
        JournalEditorAssignment.objects.create(
            user=chief,
            journal=journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            responsibilities=list(JournalEditorAssignment.ALL_RESPONSIBILITIES),
            public_name=chief.display_name,
            public_role_label="主编",
            created_by=chief,
        )

        result = check_content_readiness()
        journal_findings = {
            finding.code
            for finding in result.blockers
            if finding.target_id == str(journal.pk)
        }
        self.assertNotIn("active_journal_chief_invalid", journal_findings)

    def test_journal_scoped_readiness_ignores_other_active_journals(self):
        other = Journal.objects.create(
            name="Unrelated Journal Without Chief",
            slug="unrelated-journal-without-chief",
            az_group="U",
        )

        result = check_content_readiness(journal_ids=[self.journal.pk])

        scoped_journal_ids = {
            finding.target_id
            for finding in result.blockers
            if finding.code == "active_journal_chief_invalid"
        }
        self.assertNotIn(str(other.pk), scoped_journal_ids)

    def test_non_core_hide_navigation_policy_warns_without_blocking(self):
        self.item.code = "non-core-news"
        self.item.slug = "non-core-news"
        self.item.is_active = True
        self.item.is_visible = True
        self.item.status = "active"
        self.item.save(
            update_fields=("code", "slug", "is_active", "is_visible", "status")
        )
        self.config.empty_behavior = ColumnEmptyBehavior.HIDE_NAVIGATION
        self.config.save(update_fields=("empty_behavior",))

        result = check_content_readiness()

        self.assertNotIn(
            "column_minimum_not_met",
            {finding.code for finding in result.blockers},
            result.to_dict(),
        )
        self.assertIn(
            "column_hidden_until_minimum",
            {finding.code for finding in result.warnings},
            result.to_dict(),
        )

    def test_unapproved_placed_article_blocks_publish(self):
        article = self.create_article(
            title="Unapproved Readiness Article",
            review_status=ArticlePage.ReviewStatus.DRAFT,
        )
        self.place(article)

        result = check_content_readiness()

        self.assertIn("placement_article_not_approved", self.finding_codes(result))

    def test_missing_image_file_and_alt_block_publish(self):
        image = CustomImage.objects.create(
            title="Readiness image",
            file=uploaded_image(),
        )
        article = self.create_article(image=image)
        self.place(article)
        image.file.storage.delete(image.file.name)
        CustomImage.objects.filter(pk=image.pk).update(title="", description="")

        result = check_content_readiness()

        self.assertIn("image_alt_missing", self.finding_codes(result))
        self.assertIn("image_file_missing", self.finding_codes(result))

    def test_issue_article_outside_journal_scope_blocks_publish(self):
        self.item.target_type = NavigationTargetType.INTERNAL_PATH
        self.item.internal_path = "/readiness/"
        self.item.category = None
        self.item.save(update_fields=("target_type", "internal_path", "category"))
        other_journal = Journal.objects.create(
            name="Other Readiness Journal",
            slug="other-readiness-journal",
            az_group="O",
        )
        article = self.create_article(title="Outside Scope Article")
        issue = PublicationIssue.objects.create(
            scope=PublicationIssueScope.JOURNAL,
            journal=other_journal,
            slug="outside-scope",
            title="Outside scope issue",
            publication_date="2026-07-31",
            status=PublicationIssueStatus.PUBLISHED,
        )
        IssueArticle.objects.create(issue=issue, article=article)

        result = check_content_readiness()

        self.assertIn("issue_article_scope_mismatch", self.finding_codes(result))
        self.assertEqual(result.checked_issues, 1)

    def test_unavailable_placement_dependency_blocks_publish(self):
        article = self.create_article(title="Future Placement Article")
        placement = ArticlePlacement.objects.create(
            article=article,
            slot=self.slot,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug=self.item.placement_target_slug,
            starts_at=timezone.now() + timedelta(days=1),
        )
        target = PublishTarget(
            self.item.target_url,
            "readiness.future-placement",
            target_type="managed_content_column",
            canonical_path=self.item.target_url,
            dependencies={"placement_ids": [placement.pk]},
        )

        result = check_content_readiness(targets=[target])

        self.assertIn("placement_dependency_not_effective", self.finding_codes(result))

    def test_missing_article_static_target_blocks_publish(self):
        article = self.create_article(title="Missing Static Target Article")
        placement = self.place(article)
        column_target = PublishTarget(
            self.item.target_url,
            "readiness.column",
            target_type="managed_content_column",
            canonical_path=self.item.target_url,
            dependencies={"placement_ids": [placement.pk]},
        )

        result = check_content_readiness(targets=[column_target])

        self.assertIn("article_static_target_missing", self.finding_codes(result))

    def test_duplicate_canonical_path_blocks_publish(self):
        targets = [
            PublishTarget(
                self.item.target_url,
                "readiness.canonical-one",
                target_type="managed_content_column",
                canonical_path=self.item.target_url,
            ),
            PublishTarget(
                "/duplicate-news/",
                "readiness.canonical-two",
                target_type="managed_content_column",
                canonical_path=self.item.target_url,
            ),
        ]

        result = check_content_readiness(targets=targets)

        self.assertIn("duplicate_canonical_path", self.finding_codes(result))

    def create_homepage_article(self, title, *, image=True, alt="Homepage image alt"):
        featured_image = None
        if image:
            featured_image = CustomImage.objects.create(
                title=f"{title} image",
                file=uploaded_image(f"{title.lower().replace(' ', '-')}.png"),
            )
        return self.create_article(
            title=title,
            image=featured_image,
            image_alt=alt,
        )

    def place_on_homepage(self, article, slot_code, **overrides):
        values = {
            "article": article,
            "slot": LayoutSlot.objects.get(code=slot_code),
            "target_type": ArticlePlacement.TargetType.MAIN_SITE,
        }
        values.update(overrides)
        return ArticlePlacement.objects.create(**values)

    def test_homepage_requires_exactly_one_hero_and_two_visual_stories(self):
        self.place_on_homepage(
            self.create_homepage_article("Homepage Hero"), "home_hero"
        )
        self.place_on_homepage(
            self.create_homepage_article("Homepage Visual One"),
            "home_visual_stories",
        )
        self.place_on_homepage(
            self.create_homepage_article("Homepage Visual Two"),
            "home_visual_stories",
        )

        codes = self.finding_codes(check_homepage_readiness())

        self.assertNotIn("home_hero_count_invalid", codes)
        self.assertNotIn("home_visual_stories_count_invalid", codes)

    def test_homepage_with_only_one_visual_story_is_blocked(self):
        self.place_on_homepage(
            self.create_homepage_article("Single Visual Hero"), "home_hero"
        )
        self.place_on_homepage(
            self.create_homepage_article("Single Visual Story"),
            "home_visual_stories",
        )

        codes = self.finding_codes(check_homepage_readiness())

        self.assertIn("home_visual_stories_count_invalid", codes)

    def test_homepage_missing_alt_blocks_formal_publication(self):
        hero = self.create_homepage_article("Missing Alt Hero", alt="")
        CustomImage.objects.filter(pk=hero.featured_image_id).update(
            title="", description=""
        )
        self.place_on_homepage(hero, "home_hero", override_image_alt="")
        self.place_on_homepage(
            self.create_homepage_article("Alt Visual One"),
            "home_visual_stories",
        )
        self.place_on_homepage(
            self.create_homepage_article("Alt Visual Two"),
            "home_visual_stories",
        )

        codes = self.finding_codes(check_homepage_readiness())

        self.assertIn("homepage_image_alt_missing", codes)

    def test_homepage_placeholder_image_blocks_formal_publication(self):
        self.place_on_homepage(
            self.create_homepage_article("Placeholder Hero", image=False),
            "home_hero",
        )
        self.place_on_homepage(
            self.create_homepage_article("Placeholder Visual One"),
            "home_visual_stories",
        )
        self.place_on_homepage(
            self.create_homepage_article("Placeholder Visual Two"),
            "home_visual_stories",
        )

        with patch(
            "ai_author_forum.static_publish.readiness.get_site_settings",
            return_value=type("Settings", (), {"default_image": None})(),
        ):
            codes = self.finding_codes(check_homepage_readiness())

        self.assertIn("homepage_placeholder_image", codes)

    def test_duplicate_article_across_homepage_slots_blocks_publication(self):
        duplicate = self.create_homepage_article("Duplicate Homepage Article")
        self.place_on_homepage(duplicate, "home_hero")
        self.place_on_homepage(duplicate, "home_visual_stories")
        self.place_on_homepage(
            self.create_homepage_article("Unique Homepage Visual"),
            "home_visual_stories",
        )

        codes = self.finding_codes(check_homepage_readiness())

        self.assertIn("homepage_article_duplicate", codes)

    def test_expired_homepage_article_is_excluded_from_new_publish_snapshot(self):
        at = timezone.now()
        self.place_on_homepage(
            self.create_homepage_article("Expired Homepage Hero"),
            "home_hero",
            ends_at=at,
        )
        self.place_on_homepage(
            self.create_homepage_article("Current Homepage Visual One"),
            "home_visual_stories",
        )
        self.place_on_homepage(
            self.create_homepage_article("Current Homepage Visual Two"),
            "home_visual_stories",
        )

        codes = self.finding_codes(check_homepage_readiness(at=at))

        self.assertIn("home_hero_count_invalid", codes)
