from django.db import migrations

DEFAULT_LAYOUT_SLOTS = [
    ("home_hero", "Home hero", "home", 1, 10),
    ("home_featured", "Home featured", "home", 5, 20),
    ("latest_ai_article", "Latest AI Article", "home", 8, 30),
    ("news_block", "News block", "section", 6, 40),
    ("opinion_block", "Opinion block", "section", 6, 50),
    ("research_analysis_block", "Research Analysis block", "section", 6, 60),
    ("journal_highlights", "Journal highlights", "journal", 6, 70),
    ("journal_hero", "Journal hero", "journal", 1, 80),
    ("journal_featured", "Journal featured", "journal", 5, 90),
    ("journal_latest", "Journal latest", "journal", 20, 100),
]


def seed_default_slots(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")

    for code, title, scope, max_items, sort_order in DEFAULT_LAYOUT_SLOTS:
        LayoutSlot.objects.update_or_create(
            code=code,
            defaults={
                "title": title,
                "scope": scope,
                "max_items": max_items,
                "sort_order": sort_order,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("placements", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_slots, migrations.RunPython.noop),
    ]
