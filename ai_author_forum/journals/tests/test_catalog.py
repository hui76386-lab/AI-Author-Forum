from types import SimpleNamespace

from django.test import SimpleTestCase

from ai_author_forum.journals.catalog import (
    JOURNAL_DISCIPLINE_DEFINITIONS,
    group_journals_by_discipline,
)


class JournalCatalogueGroupingTests(SimpleTestCase):
    def test_top_120_journals_are_grouped_in_document_category_order(self):
        journals = [
            SimpleNamespace(
                name=f"Journal {position}", az_group="", sort_order=position
            )
            for position in range(1, 121)
        ]

        groups = group_journals_by_discipline(journals)

        self.assertEqual([group.code for group in groups], list("ABCDEFGHIJ"))
        self.assertEqual(
            [len(group.journals) for group in groups],
            [10, 30, 10, 10, 10, 10, 10, 10, 10, 10],
        )
        self.assertEqual(groups[0].title, JOURNAL_DISCIPLINE_DEFINITIONS[0].title)
        self.assertEqual(groups[1].journals[0].sort_order, 11)
        self.assertEqual(groups[-1].journals[-1].sort_order, 120)

    def test_active_journals_outside_the_top_120_are_not_hidden(self):
        journals = [
            SimpleNamespace(name="Draft catalogue journal", az_group="", sort_order=0)
        ]

        groups = group_journals_by_discipline(journals)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].code, "Other")
        self.assertEqual(groups[0].journals, tuple(journals))

    def test_editor_group_is_used_for_new_journal_without_catalogue_position(self):
        journal = SimpleNamespace(
            name="New biomedical journal", az_group="B", sort_order=0
        )

        groups = group_journals_by_discipline([journal])

        self.assertEqual([group.code for group in groups], ["B"])
        self.assertEqual(groups[0].journals, (journal,))

    def test_editor_group_overrides_legacy_sort_order_group(self):
        journal = SimpleNamespace(
            name="Reclassified journal", az_group="B", sort_order=1
        )

        groups = group_journals_by_discipline([journal])

        self.assertEqual([group.code for group in groups], ["B"])
        self.assertEqual(groups[0].journals, (journal,))
