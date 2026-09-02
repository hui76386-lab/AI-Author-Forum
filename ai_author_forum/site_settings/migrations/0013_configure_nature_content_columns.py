from django.db import migrations

CORE_COLUMNS = {
    "ai-article": {
        "old_path": "/explore-content/ai-article/",
        "new_path": "/sections/ai-article/",
        "template_variant": "research_list",
        "show_open_access_badge": True,
    },
    "news": {
        "old_path": "/explore-content/news/",
        "new_path": "/sections/news/",
        "template_variant": "news_landing",
    },
    "opinion": {
        "old_path": "/explore-content/opinion/",
        "new_path": "/sections/opinion/",
        "template_variant": "chronological",
    },
    "research-analysis": {
        "old_path": "/explore-content/research-analysis/",
        "new_path": "/sections/research-analysis/",
        "template_variant": "chronological",
    },
}

EDITORIAL_PAGES = {
    "careers": "Careers",
    "books-and-culture": "Books & Culture",
    "podcasts": "Podcasts",
    "videos": "Videos",
}


def _ensure_editorial_pages(site):
    # Wagtail's tree API is required to create valid path/depth/numchild values.
    from wagtail.models import Site as RuntimeSite

    from ai_author_forum.standardpages.models import IndexPage, StandardPage

    runtime_site = RuntimeSite.objects.get(pk=site.pk)
    root = runtime_site.root_page.specific
    explore = root.get_children().filter(slug="explore-content").specific().first()
    if explore is None:
        explore = IndexPage(
            title="Explore content",
            slug="explore-content",
            introduction="Editorial content and publication issue navigation.",
            body=[],
            live=True,
            show_in_menus=False,
        )
        root.add_child(instance=explore)
        explore.save_revision().publish()
    pages = {}
    for slug, title in EDITORIAL_PAGES.items():
        page = explore.get_children().filter(slug=slug).specific().first()
        if page is None:
            page = StandardPage(
                title=title,
                slug=slug,
                introduction=(
                    f"{title} content is maintained by authorised editors in Wagtail."
                ),
                body=[],
                live=True,
                show_in_menus=False,
            )
            explore.add_child(instance=page)
            page.save_revision().publish()
        pages[slug] = page
    return pages


def forwards(apps, schema_editor):
    Site = apps.get_model("wagtailcore", "Site")
    NavigationSet = apps.get_model("site_settings", "NavigationSet")
    NavigationItem = apps.get_model("site_settings", "NavigationItem")
    NavigationItemPathRedirect = apps.get_model(
        "site_settings", "NavigationItemPathRedirect"
    )
    ContentColumnConfig = apps.get_model("site_settings", "ContentColumnConfig")

    site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
    if site is None:
        return
    nav_set = NavigationSet.objects.filter(
        site=site,
        scope="main_site",
        status="active",
        is_template=False,
        journal__isnull=True,
    ).first()
    if nav_set is None:
        return

    for code, config in CORE_COLUMNS.items():
        item = NavigationItem.objects.filter(
            group__navigation_set=nav_set,
            code=code,
            target_type="internal_path",
            internal_path=config["old_path"],
        ).first()
        if item is None:
            continue
        item.target_type = "content_column"
        item.internal_path = ""
        item.external_url = ""
        item.page_id = None
        item.url = ""
        item.save(
            update_fields=(
                "target_type",
                "internal_path",
                "external_url",
                "page",
                "url",
                "updated_at",
            )
        )
        ContentColumnConfig.objects.get_or_create(
            navigation_item=item,
            defaults={
                "template_variant": config["template_variant"],
                "default_sort": "published_desc",
                "minimum_publish_items": 1,
                "empty_behavior": "block_publish",
                "show_open_access_badge": config.get("show_open_access_badge", False),
                "show_authors": True,
                "show_abstract": True,
                "enable_type_filter": code == "ai-article",
                "enable_year_filter": True,
                "page_size": 20,
            },
        )
        NavigationItemPathRedirect.objects.get_or_create(
            old_path=config["old_path"],
            defaults={
                "navigation_item": item,
                "new_path": config["new_path"],
                "http_status": 301,
                "is_active": True,
            },
        )

    # Only convert the untouched placeholders; administrator custom targets survive.
    for code, target_type, old_path in (
        ("current-issue", "current_issue", "/explore-content/current-issue/"),
        ("browse-issues", "issue_archive", "/explore-content/browse-issues/"),
    ):
        NavigationItem.objects.filter(
            group__navigation_set=nav_set,
            code=code,
            target_type="internal_path",
            internal_path=old_path,
        ).update(
            target_type=target_type,
            internal_path="",
            external_url="",
            url="",
        )

    editorial_pages = _ensure_editorial_pages(site)
    for code, page in editorial_pages.items():
        old_path = f"/explore-content/{code}/"
        NavigationItem.objects.filter(
            group__navigation_set=nav_set,
            code=code,
            target_type="internal_path",
            internal_path=old_path,
        ).update(
            target_type="wagtail_page",
            page_id=page.pk,
            internal_path="",
            external_url="",
            url="",
        )


def backwards(apps, schema_editor):
    NavigationItem = apps.get_model("site_settings", "NavigationItem")
    NavigationItemPathRedirect = apps.get_model(
        "site_settings", "NavigationItemPathRedirect"
    )
    ContentColumnConfig = apps.get_model("site_settings", "ContentColumnConfig")
    for code, config in CORE_COLUMNS.items():
        items = NavigationItem.objects.filter(code=code, target_type="content_column")
        ContentColumnConfig.objects.filter(navigation_item__in=items).delete()
        for item in items:
            item.target_type = "internal_path"
            item.internal_path = config["old_path"]
            item.save(update_fields=("target_type", "internal_path", "updated_at"))
        NavigationItemPathRedirect.objects.filter(
            old_path=config["old_path"], new_path=config["new_path"]
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("site_settings", "0012_contentcolumnconfig_default_sort_and_more"),
        ("standardpages", "0001_initial"),
        ("wagtailsearch", "0010_add_text_fields"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
