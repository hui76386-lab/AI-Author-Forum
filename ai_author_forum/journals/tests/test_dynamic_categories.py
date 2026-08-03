from django.test import TestCase

from ai_author_forum.articles.category_services import (
    ArticleCategoryError,
    validate_article_category_revision,
)
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.category_services import (
    CategoryError,
    create_category,
    move_category,
)
from ai_author_forum.journals.models import Journal, JournalCategoryPathRedirect


class DynamicCategoryTests(TestCase):
    def setUp(self):
        self.journal = Journal.objects.create(
            name="AI Ethics Forum", slug="ai-ethics", az_group="A"
        )

    def create(self, code, slug, parent=None):
        return create_category(
            journal=self.journal,
            parent=parent,
            data={"name": code.title(), "code": code, "slug": slug},
        ).category

    def test_three_level_tree_and_canonical_url(self):
        root = self.create("ROOT", "root")
        child = self.create("CHILD", "child", root)
        leaf = self.create("LEAF", "leaf", child)
        self.assertEqual(leaf.depth, 3)
        self.assertEqual(leaf.path_cache, "root/child/leaf")
        self.assertEqual(
            leaf.get_absolute_url(), "/journals/ai-ethics/categories/root/child/leaf/"
        )

    def test_duplicate_root_slug_returns_stable_error_code(self):
        self.create("FIRST", "duplicate")

        with self.assertRaises(CategoryError) as caught:
            self.create("SECOND", "duplicate")

        self.assertEqual(caught.exception.code, "CATEGORY_DUPLICATE_SLUG")

    def test_duplicate_child_slug_returns_stable_error_code(self):
        first_parent = self.create("FIRST", "first")
        second_parent = self.create("SECOND", "second")
        self.create("FIRST-CHILD", "shared", first_parent)
        # The same slug remains valid under a different parent.
        self.create("SECOND-CHILD", "shared", second_parent)

        with self.assertRaises(CategoryError) as caught:
            self.create("DUPLICATE-CHILD", "shared", first_parent)

        self.assertEqual(caught.exception.code, "CATEGORY_DUPLICATE_SLUG")

    def test_duplicate_code_returns_stable_error_code(self):
        self.create("SHARED", "first")

        with self.assertRaises(CategoryError) as caught:
            self.create("SHARED", "second")

        self.assertEqual(caught.exception.code, "CATEGORY_DUPLICATE_CODE")

    def test_fourth_level_is_rejected(self):
        root = self.create("ROOT", "root")
        child = self.create("CHILD", "child", root)
        leaf = self.create("LEAF", "leaf", child)
        with self.assertRaises(CategoryError) as caught:
            self.create("TOO-DEEP", "too-deep", leaf)
        self.assertEqual(caught.exception.code, "CATEGORY_DEPTH_EXCEEDED")

    def test_move_repaths_descendants_and_creates_redirects(self):
        first = self.create("FIRST", "first")
        second = self.create("SECOND", "second")
        child = self.create("CHILD", "child", first)
        leaf = self.create("LEAF", "leaf", child)
        move_category(
            category_id=child.pk,
            new_parent_id=second.pk,
            expected_version=child.version,
        )
        child.refresh_from_db()
        leaf.refresh_from_db()
        self.assertEqual(child.path_cache, "second/child")
        self.assertEqual(leaf.path_cache, "second/child/leaf")
        self.assertTrue(
            JournalCategoryPathRedirect.objects.filter(
                old_path="/journals/ai-ethics/categories/first/child/",
                new_path="/journals/ai-ethics/categories/second/child/",
            ).exists()
        )

    def test_category_page_and_old_path_redirect(self):
        first = self.create("FIRST", "first")
        second = self.create("SECOND", "second")
        child = self.create("CHILD", "child", first)
        self.assertEqual(self.client.get(child.get_absolute_url()).status_code, 200)
        move_category(category_id=child.pk, new_parent_id=second.pk)
        response = self.client.get("/journals/ai-ethics/categories/first/child/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"], "/journals/ai-ethics/categories/second/child/"
        )

    def test_review_requires_exactly_one_primary_category(self):
        article = ArticlePage(primary_journal=self.journal)
        with self.assertRaises(ArticleCategoryError) as caught:
            validate_article_category_revision(
                article=article, revision_content={"category_assignments": []}
            )
        self.assertEqual(caught.exception.code, "ARTICLE_PRIMARY_CATEGORY_REQUIRED")
