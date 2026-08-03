from django.db import migrations

SLOTS = (
    ("column_featured", "Content column featured", "section", 6, 150),
    ("column_list", "Content column article list", "section", 100, 160),
    ("column_sidebar", "Content column sidebar", "section", 12, 170),
)


def forwards(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    for code, title, scope, max_items, sort_order in SLOTS:
        LayoutSlot.objects.update_or_create(
            code=code,
            defaults={
                "title": title,
                "scope": scope,
                "max_items": max_items,
                "fill_mode": "manual",
                "is_system": True,
                "is_active": True,
                "sort_order": sort_order,
            },
        )


def backwards(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    LayoutSlot.objects.filter(code__in=[row[0] for row in SLOTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("placements", "0006_alter_articleplacement_options_and_more")]
    operations = [migrations.RunPython(forwards, backwards)]
