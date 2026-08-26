from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .managers import OutboxManager


class JournalInteractionPolicy(models.Model):
    class CommentsMode(models.TextChoices):
        OPEN = "open", "开放"
        READ_ONLY = "read_only", "只读"
        HIDDEN = "hidden", "隐藏"

    journal = models.OneToOneField(
        "journals.Journal",
        on_delete=models.PROTECT,
        related_name="interaction_policy",
    )
    default_comments_mode = models.CharField(
        max_length=16,
        choices=CommentsMode.choices,
        default=CommentsMode.OPEN,
    )
    default_pdf_download_enabled = models.BooleanField(default=True)
    version = models.PositiveBigIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_journal_interaction_policies",
    )
    updated_at = models.DateTimeField(auto_now=True)


class ArticleInteractionPolicy(models.Model):
    class CommentsPolicy(models.TextChoices):
        INHERIT = "inherit", "继承"
        OPEN = "open", "开放"
        READ_ONLY = "read_only", "只读"
        HIDDEN = "hidden", "隐藏"

    class PdfDownloadPolicy(models.TextChoices):
        INHERIT = "inherit", "继承"
        ENABLED = "enabled", "启用"
        DISABLED = "disabled", "禁用"

    article = models.OneToOneField(
        "articles.ArticlePage",
        on_delete=models.PROTECT,
        related_name="interaction_policy",
    )
    comments_policy = models.CharField(
        max_length=16,
        choices=CommentsPolicy.choices,
        default=CommentsPolicy.INHERIT,
    )
    pdf_download_policy = models.CharField(
        max_length=16,
        choices=PdfDownloadPolicy.choices,
        default=PdfDownloadPolicy.INHERIT,
    )
    version = models.PositiveBigIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_article_interaction_policies",
    )
    updated_at = models.DateTimeField(auto_now=True)


class ProtectedArtifactQuerySet(models.QuerySet):
    IMMUTABLE_FIELDS = {
        "article_public_id",
        "approved_revision_id",
        "release_version",
        "locale",
        "object_key",
        "mime_type",
        "byte_size",
        "sha256",
    }

    def update(self, **kwargs):
        if self.IMMUTABLE_FIELDS.intersection(kwargs):
            raise ValidationError("受保护产物内容字段只能通过产物生命周期服务写入。")
        return super().update(**kwargs)


class ProtectedArtifact(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "已请求"
        RENDERING = "rendering", "渲染中"
        VALIDATING = "validating", "校验中"
        READY = "ready", "就绪"
        ACTIVATED = "activated", "已激活"
        FAILED = "failed", "失败"
        RETIRED = "retired", "已退役"

    objects = ProtectedArtifactQuerySet.as_manager()

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    article_public_id = models.UUIDField(db_index=True)
    approved_revision_id = models.PositiveBigIntegerField()
    release_version = models.CharField(max_length=64, db_index=True)
    locale = models.CharField(max_length=32)
    object_key = models.CharField(max_length=1024, unique=True, blank=True)
    mime_type = models.CharField(max_length=120, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    renderer_version = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
    )
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("article_public_id", "release_version", "locale"),
                name="reader_artifact_article_release_locale_uniq",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).first()
            if original and original.status == self.Status.ACTIVATED:
                immutable = ProtectedArtifactQuerySet.IMMUTABLE_FIELDS
                if any(
                    getattr(self, field) != getattr(original, field)
                    for field in immutable
                ):
                    raise ValidationError(
                        "已激活受保护产物的 key/checksum/size/revision 不可修改。"
                    )
        return super().save(*args, **kwargs)


class ProtectedManifestQuerySet(models.QuerySet):
    IMMUTABLE_FIELDS = {
        "static_manifest",
        "static_manifest_id",
        "version",
        "files",
        "sha256",
        "created_at",
    }

    def update(self, **kwargs):
        if self.IMMUTABLE_FIELDS.intersection(kwargs):
            raise ValidationError("ProtectedManifest 内容字段创建后不可修改。")
        if self.filter(validation_status="activated").exists():
            raise ValidationError("已激活 ProtectedManifest 不可修改。")
        return super().update(**kwargs)

    def delete(self):
        if self.filter(validation_status="activated").exists():
            raise ValidationError("已激活 ProtectedManifest 不可删除。")
        return super().delete()


class ProtectedManifest(models.Model):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "待校验"
        VALIDATED = "validated", "已校验"
        FAILED = "failed", "失败"
        ACTIVATED = "activated", "已激活"

    objects = ProtectedManifestQuerySet.as_manager()

    static_manifest = models.OneToOneField(
        "static_publish.StaticManifest",
        on_delete=models.PROTECT,
        related_name="protected_manifest",
    )
    version = models.CharField(max_length=64, unique=True)
    files = models.JSONField(default=list)
    sha256 = models.CharField(max_length=64)
    validation_status = models.CharField(
        max_length=16,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.validation_status == self.ValidationStatus.ACTIVATED:
                raise ValidationError("已激活 ProtectedManifest 不可修改。")
            immutable = ProtectedManifestQuerySet.IMMUTABLE_FIELDS - {
                "static_manifest",
                "created_at",
            }
            if any(
                getattr(self, field) != getattr(original, field) for field in immutable
            ):
                raise ValidationError("ProtectedManifest 内容字段创建后不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.validation_status == self.ValidationStatus.ACTIVATED:
            raise ValidationError("已激活 ProtectedManifest 不可删除。")
        return super().delete(*args, **kwargs)


class ControlPlaneOutbox(models.Model):
    objects = OutboxManager()

    event_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=120, db_index=True)
    aggregate_type = models.CharField(max_length=120)
    aggregate_id = models.CharField(max_length=255)
    aggregate_version = models.PositiveBigIntegerField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("aggregate_type", "aggregate_id", "aggregate_version"),
                name="reader_cp_outbox_aggregate_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Outbox 只能通过受控 manager 更新投递状态。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Outbox 事件不可删除。")


class ModerationCommand(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        APPLIED = "applied", "已应用"
        FAILED = "failed", "失败"
        UNKNOWN = "unknown", "状态未知"

    command_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    comment_public_id = models.UUIDField(db_index=True)
    journal_id = models.PositiveBigIntegerField(db_index=True)
    article_public_id = models.UUIDField(db_index=True)
    action = models.CharField(max_length=32)
    expected_version = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=64)
    note = models.TextField(blank=True)
    idempotency_key_hash = models.CharField(
        max_length=64, blank=True, null=True, unique=True, db_index=True
    )
    request_hash = models.CharField(max_length=64, blank=True)
    result_body = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reader_moderation_commands",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    remote_event_id = models.UUIDField(null=True, blank=True, unique=True)
    request_id = models.UUIDField(default=uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
