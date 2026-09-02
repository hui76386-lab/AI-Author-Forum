from django.db import migrations, models
from django.db.models import Count


def validate_public_ids(apps, schema_editor):
    ArticlePage = apps.get_model("articles", "ArticlePage")
    manager = ArticlePage.objects.using(schema_editor.connection.alias)
    null_count = manager.filter(public_id__isnull=True).count()
    duplicate = (
        manager.values("public_id")
        .annotate(row_count=Count("pk"))
        .filter(row_count__gt=1)
        .order_by("public_id")
        .first()
    )
    if null_count or duplicate:
        raise RuntimeError(
            "ArticlePage.public_id validation failed: "
            f"null_count={null_count}, duplicate={duplicate!r}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0017_backfill_articlepage_public_id"),
    ]

    operations = [
        migrations.RunPython(validate_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="articlepage",
            name="public_id",
            field=models.UUIDField(
                db_index=True,
                editable=False,
                null=True,
                unique=True,
            ),
        ),
    ]
