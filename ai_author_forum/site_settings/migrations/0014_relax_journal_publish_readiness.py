from django.db import migrations


def allow_empty_journal_columns(apps, schema_editor):
    ContentColumnConfig = apps.get_model("site_settings", "ContentColumnConfig")
    ContentColumnConfig.objects.filter(
        navigation_item__group__navigation_set__scope="journal"
    ).update(
        minimum_publish_items=0,
        empty_behavior="editorial_message",
    )


def restore_journal_column_minimums(apps, schema_editor):
    ContentColumnConfig = apps.get_model("site_settings", "ContentColumnConfig")
    ContentColumnConfig.objects.filter(
        navigation_item__group__navigation_set__scope="journal",
        minimum_publish_items=0,
    ).update(
        minimum_publish_items=1,
        empty_behavior="block_publish",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("site_settings", "0013_configure_nature_content_columns"),
    ]

    operations = [
        migrations.RunPython(
            allow_empty_journal_columns,
            restore_journal_column_minimums,
        ),
    ]
