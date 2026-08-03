from django.core.management.base import BaseCommand
from wagtail.models import Site

from ai_author_forum.site_settings.models import (
    NavigationArea,
    NavigationItem,
    SiteSettings,
)


class Command(BaseCommand):
    help = "Create the default site settings and four-area navigation baseline."

    def handle(self, *args, **options):
        site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
        if site is None:
            self.stdout.write(
                self.style.ERROR("No Wagtail Site exists; run migrate first.")
            )
            return

        SiteSettings.objects.get_or_create(
            site=site,
            defaults={
                "site_name": "AI Author Forum",
                "static_output_root": "published",
                "core_navigation_locked": True,
            },
        )
        defaults = [
            (NavigationArea.HOME, "首页", "home", 10),
            (NavigationArea.JOURNALS, "子期刊", "journals", 20),
            (NavigationArea.ARTICLES, "文章", "articles", 30),
            (NavigationArea.ABOUT, "关于", "about", 40),
        ]
        for area, label, slug, sort_order in defaults:
            NavigationItem.objects.get_or_create(
                site=site,
                area=area,
                slug=slug,
                defaults={
                    "label": label,
                    "sort_order": sort_order,
                    "is_active": True,
                    "is_core": True,
                },
            )
        self.stdout.write(
            self.style.SUCCESS("Site settings and navigation baseline synchronized.")
        )
