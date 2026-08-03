from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def repair_article_approval_and_sync_state(apps, schema_editor):
    ArticlePage = apps.get_model("articles", "ArticlePage")
    Placement = apps.get_model("placements", "ArticlePlacement")
    Page = apps.get_model("wagtailcore", "Page")
    now = timezone.now()

    for article in ArticlePage.objects.filter(
        review_status__in=("approved", "published")
    ).iterator():
        page_state = (
            Page.objects.filter(pk=article.pk)
            .values("live_revision_id", "latest_revision_id")
            .first()
            or {}
        )
        revision_id = (
            article.approved_version_id
            or page_state.get("live_revision_id")
            or page_state.get("latest_revision_id")
        )
        update_fields = {}
        if article.approved_version_id is None and revision_id is not None:
            update_fields["approved_version_id"] = revision_id

        effective_placement_exists = (
            Placement.objects.filter(
                article_id=article.pk,
                is_active=True,
            )
            .filter(
                Q(starts_at__isnull=True) | Q(starts_at__lte=now),
                Q(ends_at__isnull=True) | Q(ends_at__gt=now),
            )
            .exists()
        )

        if revision_id is None:
            update_fields.update(
                placement_sync_status="failed",
                placement_sync_error=(
                    "Data repair could not locate an approved, live, or latest revision."
                ),
                placement_synced_revision_id=None,
                placement_sync_request_id=f"repair-article-{article.pk}-no-revision",
            )
        elif effective_placement_exists:
            update_fields.update(
                placement_sync_status="synced",
                placement_sync_error="",
                placement_synced_revision_id=revision_id,
                placement_sync_request_id=(
                    f"repair-article-{article.pk}-revision-{revision_id}"
                ),
            )
        elif article.placement_sync_status == "pending":
            update_fields.update(
                placement_sync_status="failed",
                placement_sync_error=(
                    "Data repair found no effective formal placement; review and "
                    "resynchronize this article before the next static release."
                ),
                placement_synced_revision_id=None,
                placement_sync_request_id=(
                    f"repair-article-{article.pk}-revision-{revision_id}"
                ),
            )

        if update_fields:
            ArticlePage.objects.filter(pk=article.pk).update(**update_fields)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0006_articlepage_placement_sync_error_and_more"),
        ("placements", "0007_seed_content_column_slots"),
    ]

    operations = [
        migrations.AddField(
            model_name="articlepage",
            name="placement_sync_request_id",
            field=models.CharField(
                blank=True, db_index=True, editable=False, max_length=64
            ),
        ),
        migrations.RunPython(repair_article_approval_and_sync_state, noop),
    ]
