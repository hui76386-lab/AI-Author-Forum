from django.db.models.signals import post_save
from django.dispatch import receiver

from ai_author_forum.static_publish.models import StaticPublishJob


@receiver(post_save, sender=StaticPublishJob)
def sync_related_placement_batch_status(sender, instance, **kwargs):
    # Import lazily so application loading does not create an import cycle.
    from .models import PlacementBatch
    from .publishing import sync_batch_publish_status

    for batch in PlacementBatch.objects.filter(publish_job=instance).iterator():
        sync_batch_publish_status(batch)
