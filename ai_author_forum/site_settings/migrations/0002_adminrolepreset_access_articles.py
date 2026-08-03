from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("site_settings", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="adminrolepreset",
            options={
                "ordering": ["role_code"],
                "permissions": [
                    ("access_journals", "Can access journals module"),
                    ("access_articles", "Can access articles module"),
                    ("access_article_review", "Can access article review module"),
                    ("access_placements", "Can access placements module"),
                    ("access_slots", "Can access layout slots module"),
                    ("access_static_publish", "Can access static publishing module"),
                    ("access_site_settings", "Can access site settings module"),
                    ("access_audit_log", "Can access audit log module"),
                    ("review_articles", "Can review articles"),
                    ("publish_static_site", "Can publish static site"),
                    ("rollback_static_site", "Can rollback static site"),
                    ("import_journals", "Can import journals"),
                ],
                "verbose_name": "后台角色预设",
                "verbose_name_plural": "后台角色预设",
            },
        ),
    ]
