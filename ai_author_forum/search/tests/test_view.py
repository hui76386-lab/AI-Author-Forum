from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from wagtail.models import Page


class StaticSearchViewTests(TestCase):
    def test_search_is_static_and_ignores_query_parameters(self):
        with patch.object(Page.objects, "live") as live_pages:
            response = self.client.get(reverse("search"), {"query": "ignored"})

        self.assertEqual(response.status_code, 200)
        live_pages.assert_not_called()
        self.assertContains(response, "Search AI Author Forum")
        self.assertNotContains(response, 'value="ignored"')

    def test_legacy_live_search_route_is_removed(self):
        response = self.client.get("/search/live/?query=anything")

        self.assertEqual(response.status_code, 404)
