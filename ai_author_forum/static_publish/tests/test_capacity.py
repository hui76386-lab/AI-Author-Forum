from time import perf_counter

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ai_author_forum.journals.category_services import (
    CategoryError,
    create_category,
    get_category_tree,
)
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider


class StaticPublishCapacityTests(TestCase):
    @staticmethod
    def _journals(count):
        return Journal.objects.bulk_create(
            [
                Journal(
                    name=f"Capacity Journal {index:03d}",
                    slug=f"capacity-journal-{index:03d}",
                    az_group="C",
                    sort_order=index,
                )
                for index in range(count)
            ]
        )

    @staticmethod
    def _categories(journal, count):
        return JournalCategory.objects.bulk_create(
            [
                JournalCategory(
                    journal=journal,
                    name=f"Category {index:03d}",
                    code=f"CATEGORY-{index:03d}",
                    slug=f"category-{index:03d}",
                    depth=1,
                    path_cache=f"category-{index:03d}",
                )
                for index in range(count)
            ]
        )

    def test_200_journal_targets_use_one_navigation_query(self):
        journals = self._journals(200)
        provider = WagtailPageTargetProvider()

        started_at = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            targets = provider._journal_targets(journals)
        elapsed = perf_counter() - started_at

        self.assertEqual(len(targets), 200)
        self.assertEqual(len(queries), 1)
        self.assertLess(elapsed, 5.0)
        self.assertTrue(all(target.target_type == "journal_page" for target in targets))

    def test_100_category_targets_use_bounded_bulk_queries(self):
        journal = self._journals(1)[0]
        categories = self._categories(journal, 100)
        categories[0].show_in_navigation = True
        categories[0].save(update_fields=("show_in_navigation",))
        provider = WagtailPageTargetProvider()

        started_at = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            targets = provider._category_targets(publication_time=timezone.now())
        elapsed = perf_counter() - started_at

        category_targets = [
            target for target in targets if target.target_type == "category_page"
        ]
        self.assertEqual(len(category_targets), 100)
        self.assertLessEqual(len(queries), 2)
        self.assertLess(elapsed, 5.0)
        self.assertEqual(
            {target.dependencies["category_ids"][0] for target in category_targets},
            {category.pk for category in categories},
        )

    def test_200_journal_worst_case_20000_categories_stays_query_bounded(self):
        journals = self._journals(200)
        JournalCategory.objects.bulk_create(
            [
                JournalCategory(
                    journal=journal,
                    name=f"Category {index:03d}",
                    code=f"CATEGORY-{index:03d}",
                    slug=f"category-{index:03d}",
                    depth=1,
                    path_cache=f"category-{index:03d}",
                )
                for journal in journals
                for index in range(JournalCategory.HARD_LIMIT_PER_JOURNAL)
            ],
            batch_size=500,
        )
        provider = WagtailPageTargetProvider()

        started_at = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            targets = provider._category_targets(publication_time=timezone.now())
        elapsed = perf_counter() - started_at

        self.assertEqual(len(targets), 20_000)
        self.assertLessEqual(len(queries), 2)
        self.assertLess(elapsed, 20.0)

    def test_100_node_admin_tree_uses_one_journal_partitioned_query(self):
        journal = self._journals(1)[0]
        self._categories(journal, 100)

        started_at = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            tree = get_category_tree(journal=journal)
        elapsed = perf_counter() - started_at

        self.assertEqual(len(tree), 100)
        self.assertEqual(len(queries), 1)
        self.assertLess(elapsed, 5.0)

    def test_101st_category_is_rejected_by_technical_limit(self):
        journal = self._journals(1)[0]
        self._categories(journal, JournalCategory.HARD_LIMIT_PER_JOURNAL)

        with self.assertRaises(CategoryError) as caught:
            create_category(
                journal=journal,
                data={
                    "name": "Over limit",
                    "code": "OVER-LIMIT",
                    "slug": "over-limit",
                },
            )

        self.assertEqual(caught.exception.code, "CATEGORY_LIMIT_EXCEEDED")
        self.assertEqual(
            JournalCategory.objects.filter(journal=journal).count(),
            JournalCategory.HARD_LIMIT_PER_JOURNAL,
        )
