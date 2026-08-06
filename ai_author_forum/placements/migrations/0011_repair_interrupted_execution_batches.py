from django.db import migrations
from django.utils import timezone

INTERRUPTED_STATUSES = ("draft", "validating", "ready", "executing")


def forwards(apps, schema_editor):
    PlacementBatch = apps.get_model("placements", "PlacementBatch")
    batches = PlacementBatch.objects.filter(
        executed_at__isnull=False,
        status__in=INTERRUPTED_STATUSES,
    )
    for batch in batches.iterator():
        batch.status = "failed"
        if not batch.failure_count:
            batch.failure_count = 1
        batch.updated_at = timezone.now()
        batch.save(update_fields=("status", "failure_count", "updated_at"))


class Migration(migrations.Migration):
    dependencies = [("placements", "0010_placement_workflow_v2")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
