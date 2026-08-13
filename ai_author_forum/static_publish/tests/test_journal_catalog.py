from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ai_author_forum.journals.models import Journal
from ai_author_forum.static_publish.frontend import get_journal_index_context
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider


class JournalIndexContextTests(SimpleTestCase):
    @patch("ai_author_forum.static_publish.frontend._main_navigation", return_value={})
    @patch("ai_author_forum.static_publish.frontend.get_active_journals")
    def test_configured_group_is_used_without_catalogue_position(
        self, get_active_journals, _main_navigation
    ):
        journal = SimpleNamespace(
            name="Biomedical Submission Forum",
            slug="biomedical-submission-forum",
            az_group="B",
            sort_order=0,
        )
        get_active_journals.return_value = [journal]

        context = get_journal_index_context()

        self.assertEqual([group.code for group in context["journal_groups"]], ["B"])
        self.assertEqual(context["journal_groups"][0].journals, (journal,))


class JournalIndexTargetDependencyTests(TestCase):
    def test_journal_index_renders_configured_group(self):
        journal = Journal.objects.create(
            name="Biomedical Submission Forum",
            slug="biomedical-submission-forum",
            az_group="B",
            sort_order=0,
            status="active",
        )

        response = self.client.get(reverse("journal_index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="discipline-b"', html=False)
        self.assertContains(response, journal.name)
        self.assertNotContains(response, 'id="discipline-other"', html=False)

    def test_journal_index_manifest_dependency_includes_active_journal(self):
        active_journal = Journal.objects.create(
            name="Active catalogue journal",
            slug="active-catalogue-journal",
            az_group="B",
            status="active",
        )
        paused_journal = Journal.objects.create(
            name="Paused catalogue journal",
            slug="paused-catalogue-journal",
            az_group="B",
            status="paused",
        )

        target = next(
            target
            for target in WagtailPageTargetProvider().get_targets()
            if target.url == "/journals/"
        )

        self.assertIn(active_journal.pk, target.dependencies["journal_ids"])
        self.assertNotIn(paused_journal.pk, target.dependencies["journal_ids"])
