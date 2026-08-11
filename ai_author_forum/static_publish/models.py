from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class StaticPublishJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "等待中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已成功并切换"
        PARTIAL = "partial", "部分失败"
        FAILED = "failed", "失败"
        ROLLED_BACK = "rolled_back", "已回滚"

    class Scope(models.TextChoices):
        FULL = "full", "全站发布"
        JOURNAL = "journal", "本刊发布"
        SELECTIVE = "selective", "指定路径"
        RETRY = "retry", "失败目标重试"
        ROLLBACK = "rollback", "版本回滚"

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.FULL)
    requested_paths = models.JSONField(default=list, blank=True)
    is_automatic = models.BooleanField(default=False, db_index=True)
    coalesce_key = models.CharField(max_length=64, blank=True, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    version = models.CharField(max_length=64, blank=True, db_index=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="static_publish_jobs",
    )
    retry_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="retries"
    )
    rollback_version = models.CharField(max_length=64, blank=True)
    rollback_reason = models.TextField(blank=True)
    error = models.TextField(blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(
                fields=("is_automatic", "status", "scheduled_at"),
                name="sp_job_auto_schedule_idx",
            ),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("coalesce_key",),
                condition=models.Q(is_automatic=True, status="pending"),
                name="sp_one_pending_auto_coalesce_key",
            ),
        )
        permissions = (
            ("publish_static_site", "Can publish static site"),
            ("publish_category_pages", "Can publish category pages"),
            ("retry_category_publish", "Can retry category publishing"),
            ("rollback_category_publish", "Can roll back category publishing"),
        )

    def __str__(self):
        return f"Static publish #{self.pk} ({self.get_status_display()})"


class StaticPublishTarget(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "等待中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失败"
        SKIPPED = "skipped", "已跳过"

    class Action(models.TextChoices):
        UPSERT = "upsert", "创建或更新"
        REDIRECT = "redirect", "重定向"
        DELETE = "delete", "删除"

    class ErrorCategory(models.TextChoices):
        TEMPLATE = "template", "模板渲染"
        DATA = "data", "数据缺失"
        ASSET = "asset", "素材缺失"
        FILE_WRITE = "file_write", "文件写入"
        VALIDATION = "validation", "校验失败"
        ACTIVATION = "activation", "切流失败"
        UNKNOWN = "unknown", "未知错误"

    job = models.ForeignKey(
        StaticPublishJob, on_delete=models.CASCADE, related_name="targets"
    )
    path = models.CharField(max_length=500)
    source = models.CharField(max_length=255, blank=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=255, blank=True)
    canonical_path = models.CharField(max_length=500, blank=True)
    action = models.CharField(
        max_length=16, choices=Action.choices, default=Action.UPSERT
    )
    dependencies = models.JSONField(default=dict, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    redirect_to = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    checksum = models.CharField(max_length=64, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_category = models.CharField(
        max_length=24, choices=ErrorCategory.choices, blank=True, db_index=True
    )
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("path",)
        indexes = [
            models.Index(fields=("job", "status"), name="sp_target_job_status_idx"),
            models.Index(fields=("job", "target_type"), name="sp_target_job_type_idx"),
            models.Index(
                fields=("job", "error_category"), name="sp_target_job_error_idx"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("job", "path"), name="unique_job_target_path"
            )
        ]


class StaticManifestQuerySet(models.QuerySet):
    IMMUTABLE_FIELDS = {
        "version",
        "job",
        "job_id",
        "previous_version",
        "files",
        "metadata",
        "created_at",
    }

    def update(self, **kwargs):
        protected = self.IMMUTABLE_FIELDS.intersection(kwargs)
        if protected:
            raise ValidationError(
                "Static manifests are immutable; cannot update: "
                + ", ".join(sorted(protected))
            )
        return super().update(**kwargs)


class StaticManifest(models.Model):
    IMMUTABLE_FIELDS = (
        "version",
        "job_id",
        "previous_version",
        "files",
        "metadata",
        "created_at",
    )

    objects = StaticManifestQuerySet.as_manager()

    version = models.CharField(max_length=64, unique=True)
    job = models.OneToOneField(
        StaticPublishJob, on_delete=models.PROTECT, related_name="manifest"
    )
    previous_version = models.CharField(max_length=64, blank=True)
    files = models.JSONField(default=list)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if self.pk:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(*self.IMMUTABLE_FIELDS)
                .first()
            )
            if original is not None:
                changed = [
                    field
                    for field in self.IMMUTABLE_FIELDS
                    if getattr(self, field) != original[field]
                ]
                if changed:
                    raise ValidationError(
                        "Static manifests are immutable; cannot change: "
                        + ", ".join(changed)
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Static manifests are immutable and cannot be deleted.")


class StaticBuildLog(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    job = models.ForeignKey(
        StaticPublishJob, on_delete=models.CASCADE, related_name="logs"
    )
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
