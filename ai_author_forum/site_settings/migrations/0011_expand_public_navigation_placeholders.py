from django.db import migrations
from django.utils import timezone

MAIN_GROUPS = (
    (
        "Explore content",
        "explore-content",
        (
            ("AI Article", "ai-article", "/explore-content/ai-article/"),
            ("News", "news", "/explore-content/news/"),
            ("Opinion", "opinion", "/explore-content/opinion/"),
            (
                "Research Analysis",
                "research-analysis",
                "/explore-content/research-analysis/",
            ),
            ("Careers", "careers", "/explore-content/careers/"),
            (
                "Books & Culture",
                "books-and-culture",
                "/explore-content/books-and-culture/",
            ),
            ("Podcasts", "podcasts", "/explore-content/podcasts/"),
            ("Videos", "videos", "/explore-content/videos/"),
            ("Current issue", "current-issue", "/explore-content/current-issue/"),
            ("Browse issues", "browse-issues", "/explore-content/browse-issues/"),
        ),
    ),
    (
        "Journals",
        "journals",
        (("A-Z journals", "a-z-journals", "/journals/"),),
    ),
    (
        "About the forum",
        "about-the-forum",
        (
            ("Forum Staff", "forum-staff", "/about-the-forum/forum-staff/"),
            (
                "About the Editors",
                "about-the-editors",
                "/about-the-forum/about-the-editors/",
            ),
            (
                "Research Cross-Forum Editorial Team",
                "research-cross-forum-editorial-team",
                "/about-the-forum/research-cross-forum-editorial-team/",
            ),
            (
                "Forum Information",
                "forum-information",
                "/about-the-forum/forum-information/",
            ),
            ("Forum Metrics", "forum-metrics", "/about-the-forum/forum-metrics/"),
            (
                "Our publishing models",
                "our-publishing-models",
                "/about-the-forum/our-publishing-models/",
            ),
            (
                "Editorial Values Statement",
                "editorial-values-statement",
                "/about-the-forum/editorial-values-statement/",
            ),
            (
                "Editorial policies",
                "editorial-policies",
                "/about-the-forum/editorial-policies/",
            ),
            (
                "Journalistic Principles",
                "journalistic-principles",
                "/about-the-forum/journalistic-principles/",
            ),
            (
                "Development of the Forum",
                "development-of-the-forum",
                "/about-the-forum/development-of-the-forum/",
            ),
            ("Awards", "awards", "/about-the-forum/awards/"),
            ("Contact", "contact", "/about-the-forum/contact/"),
        ),
    ),
    (
        "Co authoring with AI",
        "co-authoring-with-ai",
        (
            (
                "Definition of a co author to the AI",
                "definition-of-a-co-author-to-the-ai",
                "/co-authoring-with-ai/definition-of-a-co-author-to-the-ai/",
            ),
            (
                "Responsibility of the Co author",
                "responsibility-of-the-co-author",
                "/co-authoring-with-ai/responsibility-of-the-co-author/",
            ),
        ),
    ),
    (
        "For readers",
        "for-readers",
        (
            (
                "How AI authored Articles produced",
                "how-ai-authored-articles-produced",
                "/for-readers/how-ai-authored-articles-produced/",
            ),
            (
                "Readers responsibility",
                "readers-responsibility",
                "/for-readers/readers-responsibility/",
            ),
        ),
    ),
)

OLD_MAIN_ITEMS_TO_HIDE = ("static-recommendations", "editorial-workspace")


def upsert_main_navigation(apps, schema_editor):
    Site = apps.get_model("wagtailcore", "Site")
    NavigationSet = apps.get_model("site_settings", "NavigationSet")
    NavigationGroup = apps.get_model("site_settings", "NavigationGroup")
    NavigationItem = apps.get_model("site_settings", "NavigationItem")

    site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
    if site is None:
        return

    nav_set, _ = NavigationSet.objects.get_or_create(
        site=site,
        scope="main_site",
        status="active",
        is_template=False,
        journal=None,
        defaults={"name": "Main site navigation"},
    )

    for group_order, (group_label, group_code, items) in enumerate(
        MAIN_GROUPS, start=1
    ):
        group = NavigationGroup.objects.filter(
            navigation_set=nav_set,
            code=group_code,
        ).first()
        if group is None:
            group = NavigationGroup.objects.create(
                navigation_set=nav_set,
                code=group_code,
                label=group_label,
                sort_order=group_order,
                is_visible=True,
                status="active",
            )
        for item_order, (item_label, item_code, item_path) in enumerate(items, start=1):
            item = NavigationItem.objects.filter(
                group__navigation_set=nav_set,
                code=item_code,
            ).first()
            if item is None:
                NavigationItem.objects.create(
                    site=site,
                    area="home",
                    label=item_label,
                    slug=item_code,
                    code=item_code,
                    group=group,
                    target_type="internal_path",
                    internal_path=item_path,
                    external_url="",
                    url="",
                    open_in_new_tab=False,
                    sort_order=item_order,
                    is_active=True,
                    is_visible=True,
                    status="active",
                    allow_direct_access=True,
                    is_core=True,
                )

    NavigationItem.objects.filter(
        group__navigation_set=nav_set,
        code__in=OLD_MAIN_ITEMS_TO_HIDE,
    ).update(is_visible=False, is_active=False, status="hidden")
    NavigationGroup.objects.filter(navigation_set=nav_set).exclude(
        code__in=[group[1] for group in MAIN_GROUPS]
    ).update(is_visible=False, status="hidden")
    NavigationSet.objects.filter(pk=nav_set.pk).update(
        version=nav_set.version + 1,
        updated_at=timezone.now(),
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("site_settings", "0010_alter_adminrolepreset_options"),
    ]

    operations = [
        migrations.RunPython(upsert_main_navigation, noop_reverse),
    ]
