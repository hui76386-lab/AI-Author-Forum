from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "articles",
            "0015_remove_authorsubmissionasset_articles_author_asset_kind_payload_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="articlepage",
            name="public_id",
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
    ]
