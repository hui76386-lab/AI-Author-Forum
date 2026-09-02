from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("static_publish", "0005_automatic_placement_publish"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staticpublishjob",
            name="scope",
            field=models.CharField(
                choices=[
                    ("full", "全站发布"),
                    ("journal", "本刊发布"),
                    ("selective", "指定路径"),
                    ("retry", "失败目标重试"),
                    ("rollback", "版本回滚"),
                ],
                default="full",
                max_length=20,
            ),
        ),
    ]
