from importlib import import_module
from types import SimpleNamespace

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from wagtail.models import Locale, Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import Journal


class ArticlePublicIdMigrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.journal = Journal.objects.create(
            name="Public ID Migration Journal",
            slug="public-id-migration-journal",
            az_group="P",
        )
        self.article_ids = [
            self._create_article("Public ID Migration One").pk,
            self._create_article("Public ID Migration Two").pk,
        ]

    def _create_article(self, title):
        root_page = Page.get_first_root_node()
        if root_page is None:
            locale, _ = Locale.objects.get_or_create(
                language_code=settings.LANGUAGE_CODE
            )
            root_page = Page.add_root(
                instance=Page(title="Root", slug="root", locale=locale)
            )
        article = ArticlePage(
            title=title,
            slug=title.lower().replace(" ", "-"),
            static_slug=title.lower().replace(" ", "-"),
            abstract=f"{title} abstract",
            body=[("paragraph", f"<p>{title} body</p>")],
            authors="Migration Author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=self.journal,
            keywords="migration",
        )
        root_page.add_child(instance=article)
        return article

    def test_backfill_is_batched_idempotent_and_validated(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("articles", "0016_articlepage_public_id_expand")])
            state = executor.loader.project_state(
                [("articles", "0016_articlepage_public_id_expand")]
            )
            HistoricalArticlePage = state.apps.get_model("articles", "ArticlePage")
            HistoricalArticlePage.objects.filter(pk__in=self.article_ids).update(
                public_id=None
            )

            schema_editor = SimpleNamespace(connection=connection)
            backfill = import_module(
                "ai_author_forum.articles.migrations.0017_backfill_articlepage_public_id"
            ).backfill_public_ids
            validate = import_module(
                "ai_author_forum.articles.migrations.0018_validate_and_unique_articlepage_public_id"
            ).validate_public_ids

            backfill(state.apps, schema_editor)
            first_values = list(
                HistoricalArticlePage.objects.filter(pk__in=self.article_ids)
                .order_by("pk")
                .values_list("public_id", flat=True)
            )
            backfill(state.apps, schema_editor)
            second_values = list(
                HistoricalArticlePage.objects.filter(pk__in=self.article_ids)
                .order_by("pk")
                .values_list("public_id", flat=True)
            )
            validate(state.apps, schema_editor)

            self.assertEqual(second_values, first_values)
            self.assertTrue(all(first_values))
            self.assertEqual(len(set(first_values)), len(first_values))
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
