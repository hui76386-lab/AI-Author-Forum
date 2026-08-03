from django.core.exceptions import ValidationError
from django.test import TestCase
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot


class PlacementTargetValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.journal = Journal.objects.create(
            name="Active Journal",
            slug="active-journal",
            az_group="A",
            status="active",
        )
        cls.article = cls.create_article(
            title="Source article", slug="source-article", live=True
        )
        cls.journal_slot = LayoutSlot.objects.create(
            title="Journal feature",
            code="target-validation-journal",
            scope=LayoutSlot.Scope.JOURNAL,
        )
        cls.section_slot = LayoutSlot.objects.create(
            title="Section feature",
            code="target-validation-section",
            scope=LayoutSlot.Scope.SECTION,
        )
        cls.article_slot = LayoutSlot.objects.create(
            title="Article related",
            code="target-validation-article",
            scope=LayoutSlot.Scope.ARTICLE,
        )

    @classmethod
    def create_article(cls, *, title, slug, live):
        page = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=cls.journal,
            review_status=(
                ArticlePage.ReviewStatus.APPROVED
                if live
                else ArticlePage.ReviewStatus.DRAFT
            ),
        )
        Page.get_first_root_node().add_child(instance=page)
        if live:
            revision = page.save_revision(bypass_article_permission_check=True)
            revision.publish(skip_permission_checks=True)
            ArticlePage.objects.filter(pk=page.pk).update(
                review_status=ArticlePage.ReviewStatus.APPROVED
            )
            page.refresh_from_db()
        return page

    def placement(self, *, slot, target_type, target_slug):
        return ArticlePlacement(
            slot=slot,
            article=self.article,
            target_type=target_type,
            target_slug=target_slug,
        )

    def test_missing_or_inactive_journal_target_is_rejected(self):
        for slug in ("missing-journal", "paused-journal"):
            with self.subTest(slug=slug):
                if slug == "paused-journal":
                    Journal.objects.create(
                        name="Paused Journal",
                        slug=slug,
                        az_group="P",
                        status="paused",
                    )
                placement = self.placement(
                    slot=self.journal_slot,
                    target_type=ArticlePlacement.TargetType.JOURNAL,
                    target_slug=slug,
                )
                with self.assertRaisesMessage(
                    ValidationError, "does not exist or is not active"
                ):
                    placement.full_clean()

    def test_unknown_static_section_target_is_rejected(self):
        placement = self.placement(
            slot=self.section_slot,
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="not-a-configured-section",
        )
        with self.assertRaisesMessage(
            ValidationError, "not configured for static publishing"
        ):
            placement.full_clean()

    def test_missing_or_draft_article_target_is_rejected(self):
        self.create_article(title="Draft target", slug="draft-target", live=False)
        for slug in ("missing-target", "draft-target"):
            with self.subTest(slug=slug):
                placement = self.placement(
                    slot=self.article_slot,
                    target_type=ArticlePlacement.TargetType.ARTICLE,
                    target_slug=slug,
                )
                with self.assertRaisesMessage(
                    ValidationError, "does not exist or is not approved"
                ):
                    placement.full_clean()
