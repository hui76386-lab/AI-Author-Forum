from uuid import uuid4

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0018_validate_and_unique_articlepage_public_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="articlepage",
            name="public_id",
            field=models.UUIDField(
                db_index=True,
                default=uuid4,
                editable=False,
                unique=True,
            ),
        ),
    ]
