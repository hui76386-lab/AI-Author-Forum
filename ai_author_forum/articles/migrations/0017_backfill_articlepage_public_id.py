from uuid import uuid4

from django.db import migrations

BATCH_SIZE = 500


def backfill_public_ids(apps, schema_editor):
    ArticlePage = apps.get_model("articles", "ArticlePage")
    manager = ArticlePage.objects.using(schema_editor.connection.alias)

    while True:
        articles = list(
            manager.filter(public_id__isnull=True).order_by("pk")[:BATCH_SIZE]
        )
        if not articles:
            return
        for article in articles:
            article.public_id = uuid4()
        manager.bulk_update(articles, ["public_id"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0016_articlepage_public_id_expand"),
    ]

    operations = [
        migrations.RunPython(backfill_public_ids, migrations.RunPython.noop),
    ]
