from unittest.mock import patch

from django.apps import apps
from django.db.models.signals import post_migrate, post_save
from django.test import TestCase

from ai_author_forum.journals.models import Journal


class ManagedNavigationBootstrapTests(TestCase):
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
