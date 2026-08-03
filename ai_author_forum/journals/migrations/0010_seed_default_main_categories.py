from django.db import migrations

DEFAULT_CATEGORY_CODE = "MAIN"
DEFAULT_CATEGORY_SLUG = "main"
SYSTEM_PLACEHOLDER_SLUG = "unassigned-articles-system"


def seed_default_main_categories(apps, schema_editor):
    Journal = apps.get_model("journals", "Journal")
    JournalCategory = apps.get_model("journals", "JournalCategory")

    journal_ids = Journal.objects.exclude(slug=SYSTEM_PLACEHOLDER_SLUG).values_list(
        "pk", flat=True
    )
    for journal_id in journal_ids.iterator():
        if JournalCategory.objects.filter(
            journal_id=journal_id, code=DEFAULT_CATEGORY_CODE
        ).exists():
            continue

        root_with_main_slug = JournalCategory.objects.filter(
            journal_id=journal_id,
            parent_id__isnull=True,
            slug=DEFAULT_CATEGORY_SLUG,
        ).first()
        if root_with_main_slug is not None:
            continue

        JournalCategory.objects.create(
            journal_id=journal_id,
            parent_id=None,
            name="\u4e3b\u680f\u76ee",
            code=DEFAULT_CATEGORY_CODE,
            slug=DEFAULT_CATEGORY_SLUG,
            depth=1,
            path_cache=DEFAULT_CATEGORY_SLUG,
            description="\u7cfb\u7edf\u751f\u6210\u7684\u9ed8\u8ba4\u4e3b\u680f\u76ee\uff0c\u53ef\u5728\u540e\u53f0\u4fee\u6539\u3002",
            sort_order=0,
            status="active",
            show_in_navigation=True,
            generate_static_page=True,
            aggregate_descendants=False,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("journals", "0009_add_document_import_fields"),
    ]

    operations = [
        migrations.RunPython(seed_default_main_categories, migrations.RunPython.noop),
    ]
