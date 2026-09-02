import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from wagtail.admin.panels import FieldPanel, MultiFieldPanel


def normalize_target_slug(value):
    return (value or "").strip().strip("/")


class LayoutSlot(models.Model):
    class Scope(models.TextChoices):
        HOME = "home", "Home"
        SECTION = "section", "Section"
        JOURNAL = "journal", "Journal"
        ARTICLE = "article", "Article"
        SEARCH = "search", "Search"
        CATEGORY = "category", "Category"

    class FillMode(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Automatic fallback"

    title = models.CharField(max_length=120)
    code = models.SlugField(
        unique=True,
        max_length=80,
        help_text="Stable code used by templates, for example home_hero.",
    )
    scope = models.CharField(
        max_length=20,
        choices=Scope.choices,
        default=Scope.HOME,
    )
    max_items = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Maximum active placements for one target in this slot.",
    )
    fill_mode = models.CharField(
        max_length=20,
        choices=FillMode.choices,
        default=FillMode.MANUAL,
    )
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("title"),
                FieldPanel("code"),
                FieldPanel("scope"),
                FieldPanel("max_items"),
                FieldPanel("fill_mode"),
                FieldPanel("is_system"),
            ],
            heading="Slot definition",
        ),
        FieldPanel("description"),
        FieldPanel("is_active"),
        FieldPanel("sort_order"),
    ]

    class Meta:
        ordering = ["scope", "sort_order", "code"]
        verbose_name = "layout slot"
        verbose_name_plural = "layout slots"

    def __str__(self):
        return f"{self.title} ({self.code})"


class ArticlePlacementQuerySet(models.QuerySet):
    def _available(self, *, at, include_active_release):
        from ai_author_forum.articles.models import ArticleReviewRecord
        from ai_author_forum.journals.models import JournalStatus

        final_approval = ArticleReviewRecord.objects.filter(
            article_id=models.OuterRef("article_id"),
            action=ArticleReviewRecord.Action.FINAL_APPROVE,
            revision_id=models.OuterRef("article__approved_version_id"),
        )
        queryset = self.annotate(has_final_approval=models.Exists(final_approval))
        approval_filter = Q(has_final_approval=True)
        if include_active_release:
            from ai_author_forum.static_publish.models import StaticManifest

            active_versions = StaticManifest.objects.filter(is_active=True).values(
                "version"
            )
            approval_filter |= Q(
                article__publication_status="published",
                article__published_version__in=active_versions,
            )
        return (
            queryset.filter(
                approval_filter,
                is_active=True,
                slot__is_active=True,
                article__primary_journal__status=JournalStatus.ACTIVE,
                article__review_status__in=("approved", "published"),
                article__approved_version__isnull=False,
            )
            .filter(
                Q(starts_at__isnull=True) | Q(starts_at__lte=at),
                Q(ends_at__isnull=True) | Q(ends_at__gt=at),
            )
            .filter(
                ~Q(target_type="journal")
                | Q(article__primary_journal__slug=F("target_slug"))
            )
            .filter(
                ~Q(target_type="category")
                | Q(target_category__status__in=("active", "hidden"))
            )
            .distinct()
        )

    def available(self, at=None):
        """Return placements backed by a current chief-editor final approval."""
        return self._available(at=at or timezone.now(), include_active_release=False)

    def available_for_static_release(self, at=None):
        """Keep unchanged content from the active immutable release visible.

        The carry-forward branch never admits a new article: it requires the
        article's already-published release to be the active manifest version.
        """
        return self._available(at=at or timezone.now(), include_active_release=True)

    def for_target(self, target_type, target_slug=""):
        return self.filter(
            target_type=target_type,
            target_slug=normalize_target_slug(target_slug),
        )

    def ordered_for_display(self):
        return self.order_by("-is_pinned", "sort_order", "-created_at", "pk")


class ArticlePlacement(models.Model):
    class TargetType(models.TextChoices):
        MAIN_SITE = "main_site", "Main site"
        SECTION = "section", "Section"
        JOURNAL = "journal", "Journal"
        ARTICLE = "article", "Article"
        SEARCH = "search", "Search"
        CATEGORY = "category", "Category"

    class PlacementKind(models.TextChoices):
        AUTOMATIC_LISTING = "automatic_listing", "Automatic listing"
        FEATURED = "featured", "Featured"
        PINNED = "pinned", "Pinned"
        SIDEBAR = "sidebar", "Sidebar"

    class Source(models.TextChoices):
        SYSTEM = "system", "System"
        MANUAL = "manual", "Manual"

    slot = models.ForeignKey(
        "placements.LayoutSlot",
        on_delete=models.PROTECT,
        related_name="placements",
    )
    article = models.ForeignKey(
        "articles.ArticlePage",
        on_delete=models.PROTECT,
        related_name="placements",
    )
    target_type = models.CharField(
        max_length=20,
        choices=TargetType.choices,
        default=TargetType.MAIN_SITE,
    )
    target_slug = models.SlugField(
        max_length=120,
        blank=True,
        help_text="Use for journal, section or article targets. Leave blank for main site slots.",
    )
    target_category = models.ForeignKey(
        "journals.JournalCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="placements",
    )
    placement_kind = models.CharField(
        max_length=24,
        choices=PlacementKind.choices,
        default=PlacementKind.FEATURED,
    )
    source = models.CharField(
        max_length=12,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    metadata = models.JSONField(default=dict, blank=True)
    override_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional title used only for this placement.",
    )
    override_summary = models.TextField(
        blank=True,
        help_text="Optional summary used only for this placement.",
    )
    override_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text=(
            "Override only this placement image. Leave empty to use the article cover."
        ),
    )
    override_image_alt = models.CharField(
        max_length=255,
        blank=True,
        help_text="Alternative text used for the placement override image.",
    )
    is_pinned = models.BooleanField(
        default=False,
        help_text="Pinned items sort before normal items inside the same slot.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(blank=True, null=True)
    ends_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ArticlePlacementQuerySet.as_manager()

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("slot"),
                FieldPanel("article"),
                FieldPanel("target_type"),
                FieldPanel("target_slug"),
                FieldPanel("target_category"),
                FieldPanel("placement_kind"),
                FieldPanel("source"),
            ],
            heading="Placement target",
        ),
        MultiFieldPanel(
            [
                FieldPanel("override_title"),
                FieldPanel("override_summary"),
                FieldPanel("override_image"),
                FieldPanel("override_image_alt"),
            ],
            heading="Display overrides",
        ),
        MultiFieldPanel(
            [
                FieldPanel("is_pinned"),
                FieldPanel("sort_order"),
                FieldPanel("starts_at"),
                FieldPanel("ends_at"),
                FieldPanel("is_active"),
                FieldPanel("metadata"),
            ],
            heading="Scheduling",
        ),
    ]

    class Meta:
        ordering = ["target_type", "target_slug", "slot", "-is_pinned", "sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["slot", "article", "target_type", "target_slug"],
                condition=~Q(target_type="category"),
                name="unique_article_placement_per_target",
            ),
            models.UniqueConstraint(
                fields=["article", "target_category", "slot"],
                condition=Q(
                    source="system",
                    placement_kind="automatic_listing",
                    target_type="category",
                ),
                name="uniq_article_category_placement",
            ),
            models.CheckConstraint(
                condition=(
                    Q(target_type="category", target_category__isnull=False)
                    | (~Q(target_type="category") & Q(target_category__isnull=True))
                ),
                name="category_target_requires_fk",
            ),
        ]
        permissions = [
            ("view_system_categoryplacement", "Can view system category placements"),
            ("retry_categoryplacement_sync", "Can retry category placement sync"),
            (
                "manage_manual_categoryplacement",
                "Can manage manual category placements",
            ),
        ]
        verbose_name = "article placement"
        verbose_name_plural = "article placements"

    def __str__(self):
        return f"{self.article} -> {self.slot.code}"

    def clean(self):
        super().clean()
        self.target_slug = normalize_target_slug(self.target_slug)

        if self.target_type == self.TargetType.MAIN_SITE and self.target_slug:
            raise ValidationError("Main site placements must not define a target slug.")
        if self.target_type == self.TargetType.CATEGORY:
            if not self.target_category_id:
                raise ValidationError(
                    {
                        "target_category": "Category placements require a category target."
                    }
                )
            if self.target_slug:
                raise ValidationError(
                    {
                        "target_slug": "Category placements use target_category, not target_slug."
                    }
                )
        elif self.target_category_id:
            raise ValidationError(
                {
                    "target_category": "Only category placements may define target_category."
                }
            )
        elif self.target_type != self.TargetType.MAIN_SITE and not self.target_slug:
            raise ValidationError(
                "Target slug is required outside main site placements."
            )
        if self.target_type == self.TargetType.SEARCH and self.target_slug != "search":
            raise ValidationError(
                "Search placements must target the static search page."
            )
        self._validate_target_exists()

        expected_scopes = {
            self.TargetType.MAIN_SITE: LayoutSlot.Scope.HOME,
            self.TargetType.SECTION: LayoutSlot.Scope.SECTION,
            self.TargetType.JOURNAL: LayoutSlot.Scope.JOURNAL,
            self.TargetType.ARTICLE: LayoutSlot.Scope.ARTICLE,
            self.TargetType.SEARCH: LayoutSlot.Scope.SEARCH,
            self.TargetType.CATEGORY: LayoutSlot.Scope.CATEGORY,
        }
        if self.slot_id and self.slot.scope != expected_scopes.get(self.target_type):
            raise ValidationError(
                {"slot": "The selected slot does not match the placement target type."}
            )

        if self.article_id:
            from ai_author_forum.articles.review_services import (
                has_valid_final_approval,
            )

            allowed_statuses = {
                self.article.ReviewStatus.APPROVED,
                self.article.ReviewStatus.PUBLISHED,
            }
            if self.article.review_status not in allowed_statuses:
                raise ValidationError(
                    {"article": "Only approved articles can be placed."}
                )
            if not self.article.approved_version_id or not has_valid_final_approval(
                self.article, self.article.approved_version
            ):
                raise ValidationError(
                    {"article": "文章缺少同 revision 的主编辑终审通过记录。"}
                )
            if self.target_type == self.TargetType.JOURNAL and self.target_slug:
                belongs_to_target = (
                    self.article.primary_journal.slug == self.target_slug
                )
                if not belongs_to_target:
                    raise ValidationError(
                        "The article does not belong to this journal."
                    )
            if (
                self.target_type == self.TargetType.CATEGORY
                and self.target_category_id
                and self.target_category.journal_id != self.article.primary_journal_id
            ):
                raise ValidationError(
                    {
                        "target_category": "Category must belong to the article primary journal."
                    }
                )

        if (
            self.source == self.Source.SYSTEM
            and self.placement_kind != self.PlacementKind.AUTOMATIC_LISTING
        ):
            raise ValidationError(
                {"placement_kind": "System placements must be automatic listings."}
            )
        if (
            self.placement_kind == self.PlacementKind.AUTOMATIC_LISTING
            and self.source != self.Source.SYSTEM
        ):
            raise ValidationError(
                {"source": "Automatic listings must be system managed."}
            )

        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(
                {"ends_at": "End time must be later than start time."}
            )

        if (
            self.slot_id
            and self.is_active
            and not (
                self.source == self.Source.SYSTEM
                and self.placement_kind == self.PlacementKind.AUTOMATIC_LISTING
            )
        ):
            from .services import validate_placement_schedule

            validate_placement_schedule(self)

    def _validate_target_exists(self):
        if self.target_type == self.TargetType.JOURNAL and self.target_slug:
            from ai_author_forum.journals.models import Journal, JournalStatus

            if not Journal.objects.filter(
                slug=self.target_slug, status=JournalStatus.ACTIVE
            ).exists():
                raise ValidationError(
                    {"target_slug": "Target journal does not exist or is not active."}
                )
        elif self.target_type == self.TargetType.SECTION and self.target_slug:
            from ai_author_forum.site_settings.models import (
                NavigationEntryStatus,
                NavigationItem,
                NavigationSetStatus,
                NavigationTargetType,
            )
            from ai_author_forum.static_publish.frontend import get_static_sections

            section_slugs = {section["slug"] for section in get_static_sections()}
            managed_column_slugs = {
                item.placement_target_slug
                for item in NavigationItem.objects.filter(
                    target_type=NavigationTargetType.CONTENT_COLUMN,
                    status=NavigationEntryStatus.ACTIVE,
                    is_active=True,
                    group__navigation_set__status=NavigationSetStatus.ACTIVE,
                    group__navigation_set__is_template=False,
                ).select_related("group__navigation_set__journal")
            }
            if self.target_slug not in section_slugs | managed_column_slugs:
                raise ValidationError(
                    {
                        "target_slug": (
                            "Target section is not configured for static publishing."
                        )
                    }
                )
        elif self.target_type == self.TargetType.ARTICLE and self.target_slug:
            from ai_author_forum.articles.models import ArticlePage

            if not ArticlePage.objects.filter(
                static_slug=self.target_slug,
                review_status__in=(
                    ArticlePage.ReviewStatus.APPROVED,
                    ArticlePage.ReviewStatus.PUBLISHED,
                ),
            ).exists():
                raise ValidationError(
                    {"target_slug": "Target article does not exist or is not approved."}
                )

    def save(self, *args, **kwargs):
        self.target_slug = normalize_target_slug(self.target_slug)
        super().save(*args, **kwargs)
        from ai_author_forum.articles.publication import (
            sync_article_placement_status,
        )

        sync_article_placement_status(self.article_id)

    def delete(self, *args, **kwargs):
        article_id = self.article_id
        result = super().delete(*args, **kwargs)
        from ai_author_forum.articles.publication import (
            sync_article_placement_status,
        )

        sync_article_placement_status(article_id)
        return result

    @property
    def display_title(self):
        from ai_author_forum.utils.public_i18n import localized_article_title

        return self.override_title or localized_article_title(self.article)

    @property
    def display_summary(self):
        from ai_author_forum.utils.public_i18n import localized_article_abstract

        return self.override_summary or localized_article_abstract(self.article)

    @property
    def display_authors(self):
        from ai_author_forum.utils.public_i18n import localized_article_authors

        return localized_article_authors(self.article)

    @property
    def display_ai_coauthors(self):
        from ai_author_forum.utils.public_i18n import localized_article_ai_coauthors

        return localized_article_ai_coauthors(self.article)

    @property
    def display_image(self):
        return self.override_image


class PlacementBatch(models.Model):
    """Persistent workflow state for placement creation and maintenance.

    ``ArticlePlacement`` remains the only public placement fact.  A batch is a
    durable command/draft so a user can safely leave a workflow and return to
    it without reserving slot capacity or publishing anything.
    """

    class Mode(models.TextChoices):
        SINGLE = "single", "Single placement"
        JOURNAL_CURATION = "journal_curation", "Journal curation"
        BULK_CREATE = "bulk_create", "Bulk create"
        BULK_MAINTENANCE = "bulk_maintenance", "Bulk maintenance"

    class Operation(models.TextChoices):
        CREATE = "create", "Create"
        DEACTIVATE = "deactivate", "Deactivate"
        REACTIVATE = "reactivate", "Reactivate"
        UPDATE_SCHEDULE = "update_schedule", "Update schedule"
        PIN = "pin", "Pin"
        UNPIN = "unpin", "Unpin"
        MOVE = "move", "Move"
        COPY = "copy", "Copy"
        CANCEL_FUTURE = "cancel_future", "Cancel future"
        REPUBLISH = "republish", "Republish"
        EXPORT = "export", "Export"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATING = "validating", "Validating"
        READY = "ready", "Ready"
        EXECUTING = "executing", "Executing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class PublishStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        QUEUED = "queued", "Queued"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        PUBLISHING = "publishing", "Publishing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled back"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch_number = models.CharField(max_length=48, unique=True, editable=False)
    mode = models.CharField(max_length=24, choices=Mode.choices)
    operation = models.CharField(max_length=24, choices=Operation.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    current_step = models.CharField(max_length=24, default="article")
    strict_mode = models.BooleanField(default=True)
    target_type = models.CharField(
        max_length=20,
        choices=ArticlePlacement.TargetType.choices,
        default=ArticlePlacement.TargetType.JOURNAL,
    )
    target_slug = models.SlugField(max_length=120, blank=True)
    target_category = models.ForeignKey(
        "journals.JournalCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="placement_batches",
    )
    slot = models.ForeignKey(
        LayoutSlot,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="batches",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    options = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_placement_batches",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_placement_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    success_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    publish_status = models.CharField(
        max_length=20,
        choices=PublishStatus.choices,
        default=PublishStatus.NOT_STARTED,
    )
    publish_job = models.ForeignKey(
        "static_publish.StaticPublishJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="placement_batches",
    )

    class Meta:
        ordering = ("-updated_at", "-created_at")
        indexes = (
            models.Index(
                fields=("status", "updated_at"), name="placement_batch_status_idx"
            ),
            models.Index(
                fields=("created_by", "updated_at"), name="placement_batch_actor_idx"
            ),
        )

    def __str__(self):
        return self.batch_number or str(self.pk)

    @property
    def is_executed(self):
        # A failed preflight is still a draft: users must be able to fix it,
        # re-run validation, or delete it.  Execution failures set executed_at
        # explicitly so the immutable command history remains distinguishable.
        return (
            self.status
            in {
                self.Status.SUCCEEDED,
                self.Status.CANCELLED,
            }
            or self.executed_at is not None
        )

    def clean(self):
        super().clean()
        self.target_slug = normalize_target_slug(self.target_slug)
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError(
                {"ends_at": "End time must be later than start time."}
            )
        if self.mode == self.Mode.BULK_CREATE and not self.strict_mode:
            raise ValidationError(
                {"strict_mode": "Bulk placement always uses strict mode."}
            )
        if (
            self.target_type == ArticlePlacement.TargetType.MAIN_SITE
            and self.target_slug
        ):
            raise ValidationError(
                {"target_slug": "Main-site target must not have a slug."}
            )
        if self.target_type == ArticlePlacement.TargetType.CATEGORY:
            if not self.target_category_id:
                raise ValidationError(
                    {"target_category": "Category target requires a category."}
                )
        elif self.target_category_id:
            raise ValidationError(
                {"target_category": "Only category targets may use a category."}
            )

    def save(self, *args, **kwargs):
        self.target_slug = normalize_target_slug(self.target_slug)
        if not self.batch_number:
            self.batch_number = (
                f"PB-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_executed:
            raise ValidationError("Executed placement batches cannot be deleted.")
        return super().delete(*args, **kwargs)


class PlacementBatchItem(models.Model):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"

    class ExecutionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    batch = models.ForeignKey(
        PlacementBatch, on_delete=models.CASCADE, related_name="items"
    )
    article = models.ForeignKey(
        "articles.ArticlePage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="placement_batch_items",
    )
    placement = models.ForeignKey(
        ArticlePlacement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="batch_items",
    )
    sort_order = models.PositiveIntegerField(default=0)
    validation_status = models.CharField(
        max_length=12,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    execution_status = models.CharField(
        max_length=12, choices=ExecutionStatus.choices, default=ExecutionStatus.PENDING
    )
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    after_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "pk")
        constraints = (
            models.UniqueConstraint(
                fields=("batch", "article"),
                condition=Q(article__isnull=False),
                name="placement_batch_unique_article",
            ),
            models.UniqueConstraint(
                fields=("batch", "placement"),
                condition=Q(placement__isnull=False),
                name="placement_batch_unique_placement",
            ),
        )

    def __str__(self):
        return f"{self.batch} / {self.article or self.placement}"


class JournalUserPreference(models.Model):
    """Per-user journal favourites and recency.  It never changes Journal."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="journal_preferences",
    )
    journal = models.ForeignKey(
        "journals.Journal", on_delete=models.CASCADE, related_name="user_preferences"
    )
    is_favorite = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("user", "journal"), name="placement_user_journal_pref"
            ),
        )
        indexes = (
            models.Index(
                fields=("user", "is_favorite"), name="placement_pref_favorite_idx"
            ),
            models.Index(
                fields=("user", "last_used_at"), name="placement_pref_recent_idx"
            ),
        )

    def __str__(self):
        return f"{self.user} / {self.journal}"
