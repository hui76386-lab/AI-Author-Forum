from django.db import migrations, models

STATIC_PAGE_SLOTS = (
    ("section_top_story", "Section top story", "section", 1, 110),
    ("section_article_list", "Section article list", "section", 20, 120),
    ("section_sidebar", "Section sidebar", "section", 6, 130),
    ("search_recommended", "Search recommended articles", "search", 12, 140),
)


def seed_static_page_slots(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    for code, title, scope, max_items, sort_order in STATIC_PAGE_SLOTS:
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
    dependencies = [("placements", "0003_migrate_legacy_placements")]

    operations = [
        migrations.AlterField(
            model_name="layoutslot",
            name="scope",
            field=models.CharField(
                choices=[
                    ("home", "Home"),
                    ("section", "Section"),
                    ("journal", "Journal"),
                    ("article", "Article"),
                    ("search", "Search"),
                ],
                default="home",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="articleplacement",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("main_site", "Main site"),
                    ("section", "Section"),
                    ("journal", "Journal"),
                    ("article", "Article"),
                    ("search", "Search"),
                ],
                default="main_site",
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_static_page_slots, migrations.RunPython.noop),
    ]
