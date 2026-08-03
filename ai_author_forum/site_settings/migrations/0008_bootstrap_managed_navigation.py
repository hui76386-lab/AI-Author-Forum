from django.db import migrations

MAIN_GROUPS = (
    (
        "Explore content",
        "explore-content",
        (
            (
                "AI Article",
                "ai-article",
                "internal_path",
                "/explore-content/ai-article/",
            ),
            ("News", "news", "internal_path", "/explore-content/news/"),
            ("Opinion", "opinion", "internal_path", "/explore-content/opinion/"),
            (
                "Research Analysis",
                "research-analysis",
                "internal_path",
                "/explore-content/research-analysis/",
            ),
        ),
    ),
    (
        "Journals",
        "journals",
        (("A-Z journals", "a-z-journals", "internal_path", "/journals/"),),
    ),
    (
        "About the forum",
        "about-the-forum",
        (
            (
                "Static recommendations",
                "static-recommendations",
                "internal_path",
                "/search/",
            ),
            ("Editorial workspace", "editorial-workspace", "internal_path", "/admin/"),
        ),
    ),
)

JOURNAL_GROUPS = (
    (
        "Explore content",
        "explore-content",
        (
            ("Research articles", "research-articles", "content_column", ""),
            ("News & Comment", "news-and-comment", "content_column", ""),
            ("Current issue", "current-issue", "current_issue", ""),
            ("Browse issues", "browse-issues", "issue_archive", ""),
        ),
    ),
    (
        "About this journal",
        "about-this-journal",
        (
            (
                "Journal information",
                "journal-information",
                "internal_path",
                "/journal-information/",
            ),
            ("Contact", "contact", "internal_path", "/contact/"),
        ),
    ),
    (
        "Publish with us",
        "publish-with-us",
        (
            (
                "Author guidelines",
                "author-guidelines",
                "internal_path",
                "/author-guidelines/",
            ),
        ),
    ),
)


def create_groups(apps, nav_set, site, groups, *, journal=None):
    NavigationGroup = apps.get_model("site_settings", "NavigationGroup")
    NavigationItem = apps.get_model("site_settings", "NavigationItem")
    ContentColumnConfig = apps.get_model("site_settings", "ContentColumnConfig")
    for group_order, (label, code, items) in enumerate(groups, start=1):
        group, _ = NavigationGroup.objects.get_or_create(
            navigation_set=nav_set,
            code=code,
            defaults={"label": label, "sort_order": group_order},
        )
        for item_order, (item_label, item_code, target_type, target_value) in enumerate(
            items, start=1
        ):
            internal_path = target_value
            if journal and target_type == "internal_path":
                internal_path = f"/journals/{journal.slug}{target_value}"
            item, _ = NavigationItem.objects.get_or_create(
                group=group,
                code=item_code,
                defaults={
                    "site": site,
                    "area": "journals" if journal or nav_set.is_template else "home",
                    "label": item_label,
                    "slug": item_code,
                    "target_type": target_type,
                    "internal_path": internal_path,
                    "sort_order": item_order,
                    "is_core": not bool(journal) and not nav_set.is_template,
                    "status": "active",
                    "is_visible": True,
                    "is_active": True,
                },
            )
            if target_type == "content_column":
                ContentColumnConfig.objects.get_or_create(navigation_item=item)


def forwards(apps, schema_editor):
    Site = apps.get_model("wagtailcore", "Site")
    Journal = apps.get_model("journals", "Journal")
    NavigationSet = apps.get_model("site_settings", "NavigationSet")
    AuditLog = apps.get_model("site_settings", "AuditLog")
    site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
    if site is None:
        return
    main, main_created = NavigationSet.objects.get_or_create(
        site=site,
        scope="main_site",
        status="active",
        is_template=False,
        journal=None,
        defaults={"name": "Main site navigation"},
    )
    if main_created or not main.groups.exists():
        create_groups(apps, main, site, MAIN_GROUPS)
    template, template_created = NavigationSet.objects.get_or_create(
        site=site,
        name="Default journal navigation template",
        is_template=True,
        defaults={"scope": "journal", "status": "active"},
    )
    if template_created or not template.groups.exists():
        create_groups(apps, template, site, JOURNAL_GROUPS)
    for journal in Journal.objects.all().iterator():
        nav_set = NavigationSet.objects.filter(
            journal=journal,
            scope="journal",
            status="active",
            is_template=False,
        ).first()
        if nav_set is not None:
            continue
        nav_set = NavigationSet.objects.create(
            site=site,
            scope="journal",
            journal=journal,
            name=f"{journal.name} navigation",
            status="active",
            copied_from_template=template,
        )
        create_groups(apps, nav_set, site, JOURNAL_GROUPS, journal=journal)
    AuditLog.objects.create(
        action="configure",
        status="success",
        target_type="NavigationSet",
        target_label="Managed navigation bootstrap",
        message="Created the main navigation, default journal template and missing journal copies without overwriting existing configurations.",
        metadata={"migration": "0008_bootstrap_managed_navigation"},
    )


class Migration(migrations.Migration):
    dependencies = [
        (
            "site_settings",
            "0007_remove_navigationitem_site_settings_navigation_site_area_slug_uniq_and_more",
        )
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
