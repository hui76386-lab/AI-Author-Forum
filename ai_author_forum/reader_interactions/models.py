from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models

from .managers import ImmutableManager, OutboxManager


class ImmutableInteractionModel(models.Model):
    objects = ImmutableManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("不可变互动记录创建后不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("不可变互动记录创建后不可删除。")


class ReaderIdentity(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        SUSPENDED = "suspended", "已暂停"
        DELETED = "deleted", "已删除"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    email_ciphertext = models.TextField()
    email_lookup_hmac = models.CharField(max_length=64, unique=True)
    email_key_version = models.PositiveSmallIntegerField()
    email_verified_at = models.DateTimeField(null=True, blank=True)
    display_name = models.CharField(max_length=80)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class EmailVerificationChallenge(models.Model):
    class Purpose(models.TextChoices):
        COMMENT = "comment", "评论"
        DOWNLOAD = "download", "下载"
        SHARE = "share", "分享"
        SESSION = "session", "会话"

    class Status(models.TextChoices):
        ISSUED = "issued", "已签发"
        CONSUMED = "consumed", "已消费"
        EXPIRED = "expired", "已过期"
        SUPERSEDED = "superseded", "已替代"
        BLOCKED = "blocked", "已阻断"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    email_ciphertext = models.TextField()
    email_lookup_hmac = models.CharField(max_length=64, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    return_path = models.CharField(max_length=500)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ISSUED,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    consumed_reader = models.ForeignKey(
        "ReaderIdentity",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="consumed_verification_challenges",
    )
    consumed_session_ciphertext = models.TextField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    request_fingerprint_hmac = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("email_lookup_hmac", "purpose"),
                condition=models.Q(status="issued"),
                name="ri_one_issued_challenge_per_purpose",
            )
        ]
        indexes = [
            models.Index(
                fields=("email_lookup_hmac", "purpose", "status"),
                name="ri_challenge_email_purpose",
            )
        ]


class ReaderDeviceFlow(models.Model):
    """Short-lived pairing state linking the requesting browser to a challenge."""

    class Purpose(models.TextChoices):
        COMMENT = "comment", "评论"
        DOWNLOAD = "download", "下载"
        SHARE = "share", "分享"
        SESSION = "session", "会话"

    class Status(models.TextChoices):
        PENDING = "pending", "等待确认"
        APPROVED = "approved", "已批准"
        CLAIMED = "claimed", "已领取"
        EXPIRED = "expired", "已过期"
        CANCELLED = "cancelled", "已取消"
        SUPERSEDED = "superseded", "已替代"
        DENIED = "denied", "已拒绝"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    challenge = models.OneToOneField(
        EmailVerificationChallenge,
        on_delete=models.PROTECT,
        related_name="device_flow",
    )
    user_code_hash = models.CharField(max_length=64)
    origin_cookie_hash = models.CharField(max_length=64)
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    return_path = models.CharField(max_length=500)
    reader = models.ForeignKey(
        "ReaderIdentity",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="device_flows",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    claimed_session_ciphertext = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("status", "expires_at"), name="ri_device_flow_status_expiry"
            ),
            models.Index(
                fields=("origin_cookie_hash", "status"),
                name="ri_device_flow_origin_status",
            ),
        ]


class ReaderSession(models.Model):
    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    reader = models.ForeignKey(
        ReaderIdentity,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    secret_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()
    idle_expires_at = models.DateTimeField(db_index=True)
    absolute_expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    risk_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("reader", "revoked_at", "absolute_expires_at"),
                name="ri_session_reader_expiry",
            )
        ]


class ArticleCapabilityProjection(models.Model):
    class CommentsMode(models.TextChoices):
        OPEN = "open", "开放"
        READ_ONLY = "read_only", "只读"
        HIDDEN = "hidden", "隐藏"

    article_public_id = models.UUIDField(unique=True)
    journal_id = models.PositiveBigIntegerField(db_index=True)
    active_release = models.CharField(max_length=64, db_index=True)
    approved_revision_id = models.PositiveBigIntegerField()
    comments_mode = models.CharField(max_length=16, choices=CommentsMode.choices)
    download_enabled = models.BooleanField(default=False)
    protected_artifact_public_id = models.UUIDField(null=True, blank=True)
    policy_version = models.PositiveBigIntegerField()
    projection_version = models.PositiveBigIntegerField()
    applied_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .objects.using(kwargs.get("using") or "interactions")
                .filter(pk=self.pk)
                .first()
            )
            if previous and self.projection_version < previous.projection_version:
                raise ValidationError("能力投影版本只能单调增加。")
        return super().save(*args, **kwargs)


class Comment(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "待审核"
        PUBLISHED = "published", "已发布"
        HIDDEN = "hidden", "已隐藏"
        WITHDRAWN = "withdrawn", "已撤回"
        REJECTED = "rejected", "已拒绝"
        SPAM = "spam", "垃圾"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    article_public_id = models.UUIDField(db_index=True)
    journal_id = models.PositiveBigIntegerField(db_index=True)
    reader = models.ForeignKey(
        ReaderIdentity,
        on_delete=models.PROTECT,
        related_name="comments",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replies",
    )
    root = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="thread_comments",
    )
    body_plaintext = models.TextField()
    body_sha256 = models.CharField(max_length=64)
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.PUBLISHED,
        db_index=True,
    )
    risk_score = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        null=True,
        blank=True,
    )
    risk_labels = models.JSONField(default=list, blank=True)
    version = models.PositiveBigIntegerField(default=1)
    request_id = models.UUIDField(default=uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("article_public_id", "state", "created_at", "public_id"),
                name="ri_comment_article_state",
            ),
            models.Index(
                fields=("root", "created_at", "public_id"),
                name="ri_comment_root_created",
            ),
            models.Index(
                fields=("reader", "created_at"),
                name="ri_comment_reader_created",
            ),
        ]


class CommentModerationEvent(ImmutableInteractionModel):
    class ActorType(models.TextChoices):
        READER = "reader", "读者"
        EDITOR = "editor", "编辑"
        SYSTEM = "system", "系统"

    event_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    comment = models.ForeignKey(
        Comment,
        on_delete=models.PROTECT,
        related_name="moderation_events",
    )
    from_state = models.CharField(max_length=16, blank=True)
    to_state = models.CharField(max_length=16)
    action = models.CharField(max_length=32)
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_id = models.CharField(max_length=64)
    reason = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)
    command_id = models.UUIDField(null=True, blank=True, db_index=True)
    request_id = models.UUIDField(default=uuid4, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class CommentReport(models.Model):
    class Reason(models.TextChoices):
        SPAM = "spam", "垃圾"
        HARASSMENT = "harassment", "骚扰"
        HATE = "hate", "仇恨"
        PRIVACY = "privacy", "隐私"
        MISINFORMATION = "misinformation", "错误信息"
        OTHER = "other", "其他"

    class Status(models.TextChoices):
        OPEN = "open", "待处理"
        RESOLVED = "resolved", "已解决"
        DISMISSED = "dismissed", "已驳回"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    comment = models.ForeignKey(
        Comment,
        on_delete=models.PROTECT,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        ReaderIdentity,
        on_delete=models.PROTECT,
        related_name="comment_reports",
    )
    reason = models.CharField(max_length=24, choices=Reason.choices)
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("comment", "reporter"),
                condition=models.Q(status="open"),
                name="reader_one_open_report_per_reader",
            )
        ]
        indexes = [
            models.Index(
                fields=("comment", "status", "created_at"),
                name="ri_report_comment_status",
            )
        ]


class IdempotencyRecord(models.Model):
    reader = models.ForeignKey(
        ReaderIdentity,
        on_delete=models.PROTECT,
        related_name="idempotency_records",
    )
    scope = models.CharField(max_length=64)
    key_hash = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("reader", "scope", "key_hash"),
                name="reader_idempotency_scope_key_uniq",
            )
        ]


class DownloadGrant(models.Model):
    class Status(models.TextChoices):
        ISSUED = "issued", "已签发"
        CONSUMED = "consumed", "已消费"
        EXPIRED = "expired", "已过期"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    article_public_id = models.UUIDField(db_index=True)
    reader = models.ForeignKey(
        ReaderIdentity,
        on_delete=models.PROTECT,
        related_name="download_grants",
    )
    release_version = models.CharField(max_length=64, db_index=True)
    artifact_public_id = models.UUIDField()
    token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ISSUED,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)


class ReaderActionEvent(ImmutableInteractionModel):
    class EventType(models.TextChoices):
        SHARE_OPENED = "share_opened", "打开分享"
        LINK_COPIED = "link_copied", "复制链接"
        DOWNLOAD_GRANTED = "download_granted", "签发下载"
        DOWNLOAD_STARTED = "download_started", "开始下载"

    event_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    article_public_id = models.UUIDField(db_index=True)
    reader_public_id = models.UUIDField(null=True, blank=True, db_index=True)
    outcome = models.CharField(max_length=16, blank=True)
    request_id = models.UUIDField(default=uuid4)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("created_at", "event_type"),
                name="reader_action_created_type_idx",
            )
        ]


class InteractionOutbox(models.Model):
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
                name="reader_outbox_aggregate_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Outbox 只能通过受控 manager 更新投递状态。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Outbox 事件不可删除。")


class CommentSnapshot(ImmutableInteractionModel):
    article_public_id = models.UUIDField(db_index=True)
    version = models.PositiveBigIntegerField()
    object_key = models.CharField(max_length=1024, unique=True)
    etag = models.CharField(max_length=128)
    comment_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("article_public_id", "version"),
                name="reader_snapshot_article_version_uniq",
            )
        ]
