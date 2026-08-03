from django.core.exceptions import ValidationError
from django.test import TestCase

from ai_author_forum.journals.models import ArticlePlacement as LegacyArticlePlacement
from ai_author_forum.journals.models import (
    Journal,
    StaticArticle,
)


class JournalModelTests(TestCase):
    def test_static_site_path_defaults_from_slug(self):
        journal = Journal(name="AI Ethics Forum", slug="ai-ethics-forum", az_group="A")
        journal.full_clean()
        journal.save()

        self.assertEqual(
            journal.static_site_path, "/journals/ai-ethics-forum/index.html"
        )

    def test_slug_cannot_be_changed_by_full_clean_or_direct_save(self):
        journal = Journal.objects.create(
            name="AI Ethics Forum", slug="ai-ethics-forum", az_group="A"
        )
        journal.slug = "renamed-journal"
        with self.assertRaisesMessage(ValidationError, "cannot be changed directly"):
            journal.full_clean()
        with self.assertRaisesMessage(ValidationError, "cannot be changed directly"):
            journal.save()

        journal.refresh_from_db()
        journal.name = "AI Ethics Forum Updated"
        journal.save()
        self.assertEqual(journal.slug, "ai-ethics-forum")


class StaticArticleModelTests(TestCase):
    def test_static_output_path_uses_the_canonical_article_directory(self):
        journal = Journal.objects.create(
            name="AI Ethics Forum", slug="ai-ethics-forum", az_group="A"
        )
        article = StaticArticle(
            journal=journal,
            title="Responsible Co-authoring",
            slug="responsible-co-authoring",
        )
        article.full_clean()
        article.save()

        self.assertEqual(
            article.static_output_path,
            "/articles/responsible-co-authoring/index.html",
        )

    def test_static_output_path_cannot_be_overridden_with_a_legacy_path(self):
        journal = Journal.objects.create(
            name="AI Ethics Forum", slug="ai-ethics-forum", az_group="A"
        )
        article = StaticArticle.objects.create(
            journal=journal,
            title="Responsible Co-authoring",
            slug="responsible-co-authoring",
            static_output_path=(
                "/journals/ai-ethics-forum/articles/"
                "responsible-co-authoring/index.html"
            ),
        )

        self.assertEqual(
            article.get_absolute_url(),
            "/articles/responsible-co-authoring/",
        )
        self.assertEqual(
            article.static_output_path,
            "/articles/responsible-co-authoring/index.html",
        )


class LegacyArticlePlacementModelTests(TestCase):
    def test_new_business_writes_are_rejected(self):
        journal = Journal.objects.create(
            name="AI Ethics Forum", slug="ai-ethics-forum", az_group="A"
        )
        article = StaticArticle.objects.create(
            journal=journal,
            title="Responsible Co-authoring",
            slug="responsible-co-authoring",
        )

        with self.assertRaisesMessage(
            ValidationError, "journals.ArticlePlacement is retired"
        ):
            LegacyArticlePlacement.objects.create(
                article=article,
                slot_code="legacy-home-hero",
            )
