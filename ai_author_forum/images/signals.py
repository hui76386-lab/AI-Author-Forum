from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import CustomImage
from .references import assert_image_can_be_deleted


@receiver(
    pre_delete, sender=CustomImage, dispatch_uid="protect_referenced_custom_image"
)
def protect_referenced_custom_image(sender, instance, **kwargs):
    assert_image_can_be_deleted(instance)
