from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.urls import reverse

from ai_author_forum.journals.models import JournalCategory, JournalCategoryStatus

from ..admin_services import prepare_article_admin_row
from ..bulk_services import execute_bulk_article_action
from ..models import ArticleCategoryAssignment, ArticlePage
from ..wagtail_hooks import (
    article_category_selector_js,
    remove_article_direct_workflow_submit_action,
)
from .test_articles import ArticlePageWorkflowTests


class ArticleSubmissionCategoryFlowTests(ArticlePageWorkflowTests):
    def create_category(self, *, name, code, slug, status=JournalCategoryStatus.ACTIVE):
        return JournalCategory.objects.create(
            journal=self.journal,
            name=name,
            code=code,
            slug=slug,
            depth=1,
            path_cache=slug,
            status=status,
        )

    def test_category_options_returns_only_selectable_categories_for_primary_journal(
        self,
    ):
        selectable = self.create_category(
            name="Select me", code="SELECT", slug="select-me"
        )
        hidden = self.create_category(
            name="Hidden",
            code="HIDDEN",
            slug="hidden",
            status=JournalCategoryStatus.HIDDEN,
        )
        self.create_category(
            name="Disabled",
            code="DISABLED",
            slug="disabled",
            status=JournalCategoryStatus.DISABLED,
        )
        other_journal = self.create_journal("Other journal", "other-journal")
        other_category = JournalCategory.objects.create(
            journal=other_journal,
            name="Other",
            code="OTHER",
            slug="other",
            depth=1,
            path_cache="other",
        )
        superuser = get_user_model().objects.create_superuser(
            username="category-options-superuser",
            email="category-options-superuser@example.com",
            password="test-password",
        )
        self.client.force_login(superuser)

        response = self.client.get(
            reverse("article_admin:category_options"), {"journal": self.journal.pk}
        )

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.json()["categories"]}
        self.assertEqual(returned_ids, {selectable.pk, hidden.pk})
        self.assertNotIn(other_category.pk, returned_ids)

    def test_bulk_set_primary_category_then_submit_review_keeps_imported_article_manual(
        self,
    ):
        article = self.create_article(
            "Imported draft needs a category", assign_primary_category=False
        )
        category = self.create_category(
            name="Review ready", code="READY", slug="review-ready"
        )

        set_result = execute_bulk_article_action(
            user=self.editor,
            article_ids=[article.pk],
            action="set_primary_category",
            params={"category": category.pk},
        )
        article.refresh_from_db()

        self.assertEqual(set_result.success_count, 1)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertTrue(
            ArticleCategoryAssignment.objects.filter(
                article=article, category=category, is_primary=True
            ).exists()
        )

        submit_result = execute_bulk_article_action(
            user=self.editor,
            article_ids=[article.pk],
            action="submit_review",
        )
        article.refresh_from_db()

        self.assertEqual(submit_result.success_count, 1)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertFalse(article.publication_status)

    def test_article_row_redirects_missing_primary_category_to_assignment_instead_of_submit(
        self,
    ):
        article = self.create_article(
            "Draft without primary category", assign_primary_category=False
        )

        prepare_article_admin_row(article, user=self.editor)

        self.assertTrue(article.admin_can_submit_review)
        self.assertFalse(article.admin_has_primary_category)
        self.assertTrue(article.admin_urls["edit"])

    def test_article_editor_hides_direct_wagtail_workflow_submission(self):
        article = self.create_article("Article action menu")
        menu_items = [
            SimpleNamespace(name="action-save"),
            SimpleNamespace(name="action-submit"),
        ]

        remove_article_direct_workflow_submit_action(
            menu_items, request=None, context={"page": article}
        )

        self.assertEqual([item.name for item in menu_items], ["action-save"])

    def test_category_selector_initialises_after_dom_ready_and_explains_empty_journal(
        self,
    ):
        script = str(article_category_selector_js())

        self.assertIn("document.readyState", script)
        self.assertIn('[name$="primary_journal"]', script)
        self.assertIn("No selectable category is configured for this journal", script)
        self.assertIn("hasNewCategorySelect", script)

    def test_article_row_submit_script_does_not_cancel_inline_review_submission(self):
        from pathlib import Path

        template = Path("templates/wagtailadmin/articles/list.html").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'if (event.submitter && !event.submitter.hasAttribute("data-bulk-submit")) return;',
            template,
        )

    def test_placement_shortcut_is_hidden_for_inactive_primary_journal(self):
        inactive_journal = self.create_journal("Inactive journal", "inactive-journal")
        inactive_journal.status = "inactive"
        inactive_journal.save(update_fields=("status",))
        article = self.create_article("Inactive journal article")
        ArticlePage.objects.filter(pk=article.pk).update(
            primary_journal=inactive_journal.pk
        )
        article.refresh_from_db()

        prepare_article_admin_row(article, user=self.editor)

        self.assertEqual(article.admin_urls["place"], "")
