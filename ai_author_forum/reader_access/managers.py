from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("不可变记录创建后不可修改。")

    def delete(self):
        raise ValidationError("不可变记录创建后不可删除。")


class ImmutableManager(models.Manager.from_queryset(ImmutableQuerySet)):
    pass


class OutboxQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Outbox 只能通过受控 manager 更新投递状态。")

    def delete(self):
        raise ValidationError("Outbox 事件不可删除。")


class OutboxManager(models.Manager.from_queryset(OutboxQuerySet)):
    def mark_published(self, event_id, *, published_at=None):
        queryset = self.get_queryset().filter(
            event_id=event_id, published_at__isnull=True
        )
        return models.QuerySet.update(
            queryset,
            published_at=published_at or timezone.now(),
            last_error="",
        )

    def record_attempt(self, event_id, *, error):
        queryset = self.get_queryset().filter(event_id=event_id)
        return models.QuerySet.update(
            queryset,
            attempts=models.F("attempts") + 1,
            last_error=str(error)[:2000],
        )
