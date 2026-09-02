from django.contrib.auth import get_user_model
from django.test import TestCase
from modelcluster.forms import childformset_factory
from wagtail.models import Page

from ai_author_forum.articles.forms import ArticleCategoryAssignmentFormSet
from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.test_helpers import grant_business_super_admin


class ArticleCategoryAssignmentFormSetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="category-formset-admin",
            email="category-formset@example.com",
            password="test",
        )
        grant_business_super_admin(self.user)
        self.journal = Journal.objects.create(
            name="Formset Journal",
            slug="formset-journal",
            az_group="F",
        )
        self.category = JournalCategory.objects.create(
            journal=self.journal,
            name="Machine intelligence",
            code="MACHINE-INTELLIGENCE",
            slug="machine-intelligence",
            depth=1,
            path_cache="machine-intelligence",
        )
        self.article = ArticlePage(
            title="Recovered category assignment",
            slug="recovered-category-assignment",
            abstract="Abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=self.journal,
            owner=self.user,
        )
        Page.get_first_root_node().add_child(instance=self.article)
        self.assignment = ArticleCategoryAssignment.objects.create(
            article=self.article,
            category=self.category,
            is_primary=False,
        )

    def test_single_existing_category_is_automatically_promoted_to_primary(self):
        formset_class = childformset_factory(
            ArticlePage,
            ArticleCategoryAssignment,
            formset=ArticleCategoryAssignmentFormSet,
            fields=["category", "is_primary"],
            extra=0,
        )
        prefix = "category_assignments"
        formset = formset_class(
            data={
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "1",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
                f"{prefix}-0-id": str(self.assignment.pk),
                f"{prefix}-0-category": str(self.category.pk),
            },
            instance=self.article,
            prefix=prefix,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.article.save()

        self.assignment.refresh_from_db()
        self.assertTrue(self.assignment.is_primary)

    def test_missing_hidden_child_id_reuses_existing_category_assignment(self):
        formset_class = childformset_factory(
            ArticlePage,
            ArticleCategoryAssignment,
            formset=ArticleCategoryAssignmentFormSet,
            fields=["category", "is_primary"],
            extra=0,
        )
        prefix = "category_assignments"
        formset = formset_class(
            data={
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
                f"{prefix}-0-id": "",
                f"{prefix}-0-category": str(self.category.pk),
                f"{prefix}-0-is_primary": "on",
            },
            instance=self.article,
            prefix=prefix,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.article.save()

        assignments = list(
            ArticleCategoryAssignment.objects.filter(article=self.article)
        )
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].pk, self.assignment.pk)
        self.assertTrue(assignments[0].is_primary)
