from unittest.mock import patch

from django.apps import apps
from django.db.models.signals import post_migrate, post_save
from django.test import TestCase

from ai_author_forum.journals.models import Journal
from ai_author_forum.site_settings.models import (
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
)


class ManagedNavigationBootstrapTests(TestCase):
    def test_new_journal_receives_an_active_navigation_set(self):
        journal = Journal.objects.create(
            name="Signal navigation journal",
            slug="signal-navigation-journal",
            az_group="S",
        )

        self.assertTrue(
            NavigationSet.objects.filter(
                journal=journal,
                scope=NavigationScope.JOURNAL,
                status=NavigationSetStatus.ACTIVE,
                is_template=False,
            ).exists()
        )

    @patch("wagtail.models.Site.objects.exists", return_value=False)
    def test_post_migrate_skips_bootstrap_until_a_wagtail_site_exists(self, exists):
        app_config = apps.get_app_config("site_settings")

        post_migrate.send(
            sender=app_config,
            app_config=app_config,
            verbosity=0,
            interactive=False,
            using="default",
            plan=[],
        )

        exists.assert_called_once_with()

    @patch("wagtail.models.Site.objects.exists", return_value=False)
    def test_new_journal_skips_navigation_copy_until_a_wagtail_site_exists(
        self, exists
    ):
        post_save.send(
            sender=Journal,
            instance=Journal(name="No site", slug="no-site", az_group="N"),
            created=True,
            raw=False,
            using="default",
            update_fields=None,
        )

        exists.assert_called_once_with()
