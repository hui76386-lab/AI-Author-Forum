import django.db.models.deletion
from django.db import migrations, models


def seed_home_visual_stories(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    LayoutSlot.objects.update_or_create(
        code="home_visual_stories",
        defaults={
            "title": "首页视觉推荐",
            "scope": "home",
            "max_items": 2,
            "fill_mode": "manual",
            "description": "首页 Research Highlights 两篇有序视觉推荐文章。",
            "is_system": True,
            "is_active": True,
            "sort_order": 20,
        },
    )


def remove_home_visual_stories(apps, schema_editor):
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    LayoutSlot.objects.filter(code="home_visual_stories").delete()


class Migration(migrations.Migration):
    dependencies = [("placements", "0008_column_secondary_and_capacities")]

    operations = [
        migrations.AddField(
            model_name="articleplacement",
            name="override_image_alt",
            field=models.CharField(
                blank=True,
                help_text="Alternative text used for the placement override image.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="articleplacement",
            name="override_image",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Override only this placement image. Leave empty to use the article cover."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="images.customimage",
            ),
        ),
        migrations.RunPython(seed_home_visual_stories, remove_home_visual_stories),
    ]
