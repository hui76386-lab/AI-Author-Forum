from django.apps import AppConfig
from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_migrate, post_save


class SiteSettingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_author_forum.site_settings"

    def ready(self):
        from ai_author_forum.journals.models import Journal
        from ai_author_forum.site_settings.navigation import (
            ensure_default_journal_navigation_template,
            ensure_main_navigation_set,
            ensure_navigation_for_journal,
        )

        def copy_default_navigation(sender, instance, created, **kwargs):
            if not created:
                return
            try:
                ensure_navigation_for_journal(instance)
            except (OperationalError, ProgrammingError):
                return

        def bootstrap_managed_navigation(sender, **kwargs):
            if sender.label != self.label:
                return
            try:
                ensure_main_navigation_set()
                ensure_default_journal_navigation_template()
                for journal in Journal.objects.all().iterator():
                    ensure_navigation_for_journal(journal)
            except (OperationalError, ProgrammingError):
                return

        post_save.connect(
            copy_default_navigation,
            sender=Journal,
            dispatch_uid="site_settings.copy_default_navigation_to_new_journal",
        )
        post_migrate.connect(
            bootstrap_managed_navigation,
            sender=self,
            dispatch_uid="site_settings.bootstrap_managed_navigation",
        )
