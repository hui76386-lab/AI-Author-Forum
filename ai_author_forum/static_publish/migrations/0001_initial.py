import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="StaticPublishJob",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("partial", "Partially failed"),
                            ("failed", "Failed"),
                            ("rolled_back", "Rolled back"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("full", "Full site"),
                            ("selective", "Selected pages"),
                            ("retry", "Failed pages retry"),
                            ("rollback", "Rollback"),
                        ],
                        default="full",
                        max_length=20,
                    ),
                ),
                ("requested_paths", models.JSONField(blank=True, default=list)),
                ("version", models.CharField(blank=True, db_index=True, max_length=64)),
                ("rollback_version", models.CharField(blank=True, max_length=64)),
                ("error", models.TextField(blank=True)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "retry_of",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="retries",
                        to="static_publish.staticpublishjob",
                    ),
                ),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="static_publish_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "permissions": (
                    ("publish_static_site", "Can publish and roll back static site"),
                ),
            },
        ),
        migrations.CreateModel(
            name="StaticPublishTarget",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("path", models.CharField(max_length=500)),
                ("source", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("checksum", models.CharField(blank=True, max_length=64)),
                ("size", models.PositiveBigIntegerField(default=0)),
                ("duration_ms", models.PositiveIntegerField(default=0)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="targets",
                        to="static_publish.staticpublishjob",
                    ),
                ),
            ],
            options={"ordering": ("path",)},
        ),
        migrations.CreateModel(
            name="StaticManifest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.CharField(max_length=64, unique=True)),
                ("previous_version", models.CharField(blank=True, max_length=64)),
                ("files", models.JSONField(default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="manifest",
                        to="static_publish.staticpublishjob",
                    ),
                ),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="StaticBuildLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "level",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("warning", "Warning"),
                            ("error", "Error"),
                        ],
                        default="info",
                        max_length=10,
                    ),
                ),
                ("message", models.TextField()),
                ("context", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="logs",
                        to="static_publish.staticpublishjob",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "pk")},
        ),
        migrations.AddConstraint(
            model_name="staticpublishtarget",
            constraint=models.UniqueConstraint(
                fields=("job", "path"), name="unique_job_target_path"
            ),
        ),
    ]
