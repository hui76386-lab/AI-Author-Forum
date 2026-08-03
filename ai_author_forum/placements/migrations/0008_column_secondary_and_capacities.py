from django.db import migrations


def forwards(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    LayoutSlot.objects.get_or_create(
        code="column_secondary",
        defaults={
            "title": "Content column secondary",
            "scope": "section",
            "max_items": 3,
            "fill_mode": "manual",
            "is_system": True,
            "is_active": True,
            "sort_order": 155,
        },
    )
    # Only normalise untouched legacy defaults; preserve administrator changes.
    LayoutSlot.objects.filter(code="column_featured", max_items=6).update(max_items=1)
    LayoutSlot.objects.filter(code="column_list", max_items=100).update(max_items=20)
    LayoutSlot.objects.filter(code="column_sidebar", max_items=12).update(max_items=8)


def backwards(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    LayoutSlot.objects.filter(code="column_secondary").delete()
    LayoutSlot.objects.filter(code="column_featured", max_items=1).update(max_items=6)
    LayoutSlot.objects.filter(code="column_list", max_items=20).update(max_items=100)
    LayoutSlot.objects.filter(code="column_sidebar", max_items=8).update(max_items=12)


class Migration(migrations.Migration):
    dependencies = [("placements", "0007_seed_content_column_slots")]
    operations = [migrations.RunPython(forwards, backwards)]
