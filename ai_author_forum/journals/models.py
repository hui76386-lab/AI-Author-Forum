import re

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import get_language
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.search import index
from wagtail.snippets.models import register_snippet

from ai_author_forum.images.models import CustomImage

from .blocks import HeroQuickLinkBlock
from .validators import validate_controlled_rich_text, validate_public_link


class JournalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ARCHIVED = "archived", "Archived"


class ImportJobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VALIDATING = "validating", "Validating"
    READY = "ready", "Ready"
    IMPORTING = "importing", "Importing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class ArticleImportScope(models.TextChoices):
    GLOBAL = "global", "Global"
    JOURNAL = "journal", "Journal"


class ImportRowStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"
    UPDATED = "updated", "Updated"


class ArticleType(models.TextChoices):
    AI_ARTICLE = "ai_article", "AI Article"
    NEWS = "news", "News"
    OPINION = "opinion", "Opinion"
    REVIEW = "review", "Review"
    EDITORIAL = "editorial", "Editorial"


class ArticleReviewStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    PLACED = "placed", "Placed"
    BUILT = "built", "Built"
    PUBLISHED = "published", "Published"
    OFFLINE = "offline", "Offline"


class LayoutScope(models.TextChoices):
    MAIN = "main", "Main site"
    JOURNAL = "journal", "Journal site"
    SECTION = "section", "Section"
    SEARCH = "search", "Search"


class LayoutType(models.TextChoices):
    HERO = "hero", "Hero"
    LIST = "list", "List"
    GRID = "grid", "Grid"
    RAIL = "rail", "Rail"


class SourceMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    AUTO = "auto", "Automatic"


class PlacementStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class AssetBindingStatus(models.TextChoices):
    BOUND = "bound", "Bound"
    PENDING = "pending", "Pending"
    MISSING = "missing", "Missing"


def import_upload_to(instance, filename):
    return f"journal-imports/{instance.__class__.__name__.lower()}/{filename}"


@register_snippet
class Journal(models.Model):
    name = models.CharField(max_length=255)
    name_cn = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(max_length=255, unique=True)
    az_group = models.CharField(
        max_length=1,
    )
    status = models.CharField(
        max_length=20,
        choices=JournalStatus.choices,
        default=JournalStatus.ACTIVE,
    )
    sort_order = models.PositiveIntegerField(
        default=0, validators=[MinValueValidator(0)]
    )
    cover_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    metrics_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.TextField(blank=True)
    homepage_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link", "ul", "ol"],
        validators=[validate_controlled_rich_text],
    )
    hero_kicker = models.CharField(
        max_length=80,
        default="期刊主页",
    )
    hero_primary_cta_text = models.CharField(
        max_length=80,
        blank=True,
        default="探索人工智能文章",
    )
    hero_primary_cta_url = models.CharField(
        max_length=500,
        blank=True,
        default="/explore-content/ai-article/",
        validators=[validate_public_link],
    )
    hero_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    hero_image_alt = models.CharField(
        max_length=255,
        blank=True,
    )
    hero_quick_links = StreamField(
        [("link", HeroQuickLinkBlock())],
        blank=True,
        max_num=6,
        use_json_field=True,
    )
    static_site_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Static homepage path for this journal. Defaults to /journals/{slug}/index.html.",
    )
    target_article_count = models.PositiveSmallIntegerField(default=100)
    notes = models.TextField(blank=True)
    show_editorial_team_on_article_pages = models.BooleanField(default=True)
    editorial_team_heading = models.CharField(max_length=80, default="编辑团队")
    accepts_author_submissions = models.BooleanField(
        default=False,
        db_index=True,
        help_text="仅控制作者投稿入口；期刊状态、关闭时间和有效主编辑仍会再次校验。",
    )
    submission_guidelines_url = models.CharField(
        max_length=500,
        blank=True,
        validators=[validate_public_link],
    )
    submission_opened_at = models.DateTimeField(null=True, blank=True)
    submission_closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    search_fields = [
        index.SearchField("name"),
        index.SearchField("name_cn"),
        index.SearchField("slug"),
    ]

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("name"),
                FieldPanel("name_cn"),
                FieldPanel("slug"),
                FieldPanel("az_group"),
                FieldPanel("status"),
                FieldPanel("sort_order"),
            ],
            heading="Basic information",
        ),
        MultiFieldPanel(
            [
                FieldPanel("hero_kicker"),
                FieldPanel("homepage_intro"),
                FieldPanel("hero_image"),
                FieldPanel("hero_image_alt"),
                FieldPanel("hero_primary_cta_text"),
                FieldPanel("hero_primary_cta_url"),
                FieldPanel("hero_quick_links"),
            ],
            heading="Journal homepage Hero",
        ),
        MultiFieldPanel(
            [
                FieldPanel("cover_image"),
                FieldPanel("metrics_image"),
                FieldPanel("target_article_count"),
                FieldPanel("show_editorial_team_on_article_pages"),
                FieldPanel("editorial_team_heading"),
            ],
            heading="Journal resources",
        ),
        MultiFieldPanel(
            [
                FieldPanel("accepts_author_submissions"),
                FieldPanel("submission_guidelines_url"),
                FieldPanel("submission_opened_at"),
                FieldPanel("submission_closed_at"),
            ],
            heading="Author submissions",
        ),
        MultiFieldPanel(
            [
                FieldPanel("seo_title"),
                FieldPanel("seo_description"),
                FieldPanel("static_site_path"),
                FieldPanel("notes"),
            ],
            heading="SEO and static publishing",
        ),
    ]

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "journal"
        verbose_name_plural = "journals"
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                name="journals_journal_slug_unique",
            )
        ]

    def clean(self):
        super().clean()
        self._validate_slug_immutable()
        if self.hero_quick_links:
            try:
                self._meta.get_field("hero_quick_links").stream_block.clean(
                    self.hero_quick_links
                )
            except ValidationError as exc:
                raise ValidationError({"hero_quick_links": exc}) from exc
        if self.name and not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        if self.az_group:
            self.az_group = self.az_group.upper()
        if self.az_group and not re.fullmatch(r"[A-Z#]", self.az_group):
            raise ValidationError({"az_group": "Use A-Z or #."})
        if (
            self.submission_opened_at
            and self.submission_closed_at
            and self.submission_closed_at <= self.submission_opened_at
        ):
            raise ValidationError(
                {"submission_closed_at": "投稿关闭时间必须晚于投稿开放时间。"}
            )
        if not self.static_site_path and self.slug:
            self.static_site_path = f"/journals/{self.slug}/index.html"

    def save(self, *args, **kwargs):
        self._validate_slug_immutable()
        if not self.static_site_path and self.slug:
            self.static_site_path = f"/journals/{self.slug}/index.html"
        return super().save(*args, **kwargs)

    def _validate_slug_immutable(self):
        if not self.pk or getattr(self, "_allow_slug_migration", False):
            return
        original_slug = (
            type(self).objects.filter(pk=self.pk).values_list("slug", flat=True).first()
        )
        if original_slug is not None and original_slug != self.slug:
            raise ValidationError(
                {
                    "slug": (
                        "Journal slug cannot be changed directly. Use the audited "
                        "slug migration and redirect workflow."
                    )
                }
            )

    def __str__(self):
        # Wagtail chooser labels call ``str(instance)``.  Returning the
        # Chinese editorial name unconditionally made every English chooser
        # value get replaced by the response sanitizer.
        if (get_language() or "").lower().startswith("en"):
            return self.name or self.name_cn or self.slug
        return self.name_cn or self.name or self.slug


class JournalEditorAssignmentQuerySet(models.QuerySet):
    def effective(self, *, at=None):
        at = at or timezone.now()
        return self.filter(
            is_active=True,
            user__is_active=True,
            user__account_status="active",
            journal__status=JournalStatus.ACTIVE,
        ).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=at),
            Q(ends_at__isnull=True) | Q(ends_at__gt=at),
        )


class JournalEditorAssignment(models.Model):
    class Role(models.TextChoices):
        CHIEF_EDITOR = "chief_editor", "主编辑"
        EXECUTIVE_EDITOR = "executive_editor", "常务副编辑"
        ASSOCIATE_EDITOR = "associate_editor", "副编辑"

    class Responsibility(models.TextChoices):
        ARTICLE_MAINTENANCE = "article_maintenance", "文章维护"
        JOURNAL_PROFILE = "journal_profile", "期刊资料"
        COLUMN_NAVIGATION = "column_navigation", "栏目与导航"
        ISSUE_MANAGEMENT = "issue_management", "期次管理"
        MEDIA_ASSETS = "media_assets", "图片与素材"

    DEFAULT_PUBLIC_ROLE_LABELS = {
        Role.CHIEF_EDITOR: "主编",
        Role.EXECUTIVE_EDITOR: "执行主编",
        Role.ASSOCIATE_EDITOR: "副主编",
    }
    ALLOWED_PUBLIC_ROLE_LABELS = {
        Role.CHIEF_EDITOR: {"主编", "主编辑"},
        Role.EXECUTIVE_EDITOR: {"执行主编", "常务副编辑"},
        Role.ASSOCIATE_EDITOR: {"副主编", "副编辑"},
    }
    ALL_RESPONSIBILITIES = tuple(value for value, _label in Responsibility.choices)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="journal_editor_assignments",
    )
    journal = models.ForeignKey(
        Journal,
        on_delete=models.PROTECT,
        related_name="editor_assignments",
    )
    role = models.CharField(max_length=24, choices=Role.choices)
    responsibilities = models.JSONField(default=list, blank=True)
    public_name = models.CharField(max_length=255)
    public_affiliation = models.CharField(max_length=500, blank=True)
    public_role_label = models.CharField(max_length=40)
    display_order = models.PositiveIntegerField(default=0)
    show_publicly = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True, default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_journal_editor_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ended_journal_editor_assignments",
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.TextField(blank=True)
    replaced_by_assignment = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replaced_assignments",
    )

    objects = JournalEditorAssignmentQuerySet.as_manager()

    class Meta:
        ordering = ["journal_id", "role", "display_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["journal"],
                condition=Q(is_active=True, role="chief_editor"),
                name="journals_one_active_chief_editor",
            ),
            models.UniqueConstraint(
                fields=["journal"],
                condition=Q(is_active=True, role="executive_editor"),
                name="journals_one_active_executive_editor",
            ),
            models.UniqueConstraint(
                fields=["user", "journal", "role"],
                condition=Q(is_active=True),
                name="journals_unique_active_editor_role",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | Q(starts_at__isnull=True)
                | Q(ends_at__gt=models.F("starts_at")),
                name="journals_editor_assignment_dates_valid",
            ),
        ]

    def clean(self):
        super().clean()
        responsibilities = list(dict.fromkeys(self.responsibilities or []))
        invalid = set(responsibilities) - set(self.ALL_RESPONSIBILITIES)
        if invalid:
            raise ValidationError(
                {
                    "responsibilities": f"Unsupported responsibilities: {', '.join(sorted(invalid))}"
                }
            )
        if self.role == self.Role.ASSOCIATE_EDITOR and not responsibilities:
            raise ValidationError({"responsibilities": "副编辑至少需要一项维护职责。"})
        if self.role in {self.Role.CHIEF_EDITOR, self.Role.EXECUTIVE_EDITOR}:
            responsibilities = list(self.ALL_RESPONSIBILITIES)
        self.responsibilities = responsibilities
        allowed_labels = self.ALLOWED_PUBLIC_ROLE_LABELS.get(self.role, set())
        if self.public_role_label not in allowed_labels:
            raise ValidationError(
                {"public_role_label": "前台角色名称与任命角色不匹配。"}
            )
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "任期结束时间必须晚于开始时间。"})
        if self.user_id and self.journal_id and self.role:
            overlapping = (
                type(self)
                .objects.filter(
                    user_id=self.user_id,
                    journal_id=self.journal_id,
                    role=self.role,
                )
                .exclude(pk=self.pk)
            )
            if self.ends_at is not None:
                overlapping = overlapping.filter(
                    Q(starts_at__isnull=True) | Q(starts_at__lt=self.ends_at)
                )
            if self.starts_at is not None:
                overlapping = overlapping.filter(
                    Q(ends_at__isnull=True) | Q(ends_at__gt=self.starts_at)
                )
            if overlapping.exists():
                raise ValidationError(
                    {"starts_at": "同一用户、期刊和角色的任期不能重叠。"}
                )

    def save(self, *args, **kwargs):
        if not self.public_name:
            self.public_name = self.user.display_name
        if not self.public_role_label:
            self.public_role_label = self.DEFAULT_PUBLIC_ROLE_LABELS[self.role]
        self.full_clean()
        return super().save(*args, **kwargs)

    def is_effective(self, *, at=None):
        at = at or timezone.now()
        return bool(
            self.is_active
            and self.user.is_active
            and self.user.account_status == "active"
            and self.journal.status == JournalStatus.ACTIVE
            and (self.starts_at is None or self.starts_at <= at)
            and (self.ends_at is None or self.ends_at > at)
        )

    def __str__(self):
        role = self.get_role_display()
        if (get_language() or "").lower().startswith("en"):
            role = {
                self.Role.CHIEF_EDITOR: "Chief editor",
                self.Role.EXECUTIVE_EDITOR: "Executive editor",
                self.Role.ASSOCIATE_EDITOR: "Associate editor",
            }.get(self.role, role)
        return f"{self.journal}: {self.public_name} ({role})"


class PublicationIssueScope(models.TextChoices):
    MAIN_SITE = "main_site", "Main site"
    JOURNAL = "journal", "Journal"


class PublicationIssueStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


@register_snippet
class PublicationIssue(models.Model):
    scope = models.CharField(
        max_length=20,
        choices=PublicationIssueScope.choices,
        default=PublicationIssueScope.MAIN_SITE,
    )
    journal = models.ForeignKey(
        "journals.Journal",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="publication_issues",
    )
    slug = models.SlugField(max_length=120)
    volume_label = models.CharField(max_length=64, blank=True)
    issue_number = models.CharField(max_length=64, blank=True)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    cover_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="publication_issues",
    )
    publication_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=PublicationIssueStatus.choices,
        default=PublicationIssueStatus.DRAFT,
    )
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("scope"),
                FieldPanel("journal"),
                FieldPanel("slug"),
            ],
            heading="Scope",
        ),
        MultiFieldPanel(
            [
                FieldPanel("title"),
                FieldPanel("volume_label"),
                FieldPanel("issue_number"),
                FieldPanel("publication_date"),
                FieldPanel("summary"),
                FieldPanel("cover_image"),
            ],
            heading="Issue content",
        ),
    ]

    class Meta:
        ordering = ["-publication_date", "-pk"]
        verbose_name = "publication issue"
        verbose_name_plural = "publication issues"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=PublicationIssueScope.MAIN_SITE, journal__isnull=True)
                    | Q(scope=PublicationIssueScope.JOURNAL, journal__isnull=False)
                ),
                name="journals_issue_scope_matches_journal",
            ),
            models.CheckConstraint(
                condition=(
                    Q(is_current=False) | Q(status=PublicationIssueStatus.PUBLISHED)
                ),
                name="journals_current_issue_is_published",
            ),
            models.UniqueConstraint(
                fields=["slug"],
                condition=Q(scope=PublicationIssueScope.MAIN_SITE),
                name="journals_main_issue_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["journal", "slug"],
                condition=Q(scope=PublicationIssueScope.JOURNAL),
                name="journals_journal_issue_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["scope"],
                condition=Q(
                    scope=PublicationIssueScope.MAIN_SITE,
                    is_current=True,
                ),
                name="journals_one_current_main_issue",
            ),
            models.UniqueConstraint(
                fields=["journal"],
                condition=Q(
                    scope=PublicationIssueScope.JOURNAL,
                    is_current=True,
                ),
                name="journals_one_current_journal_issue",
            ),
        ]
        permissions = [
            ("publish_publication_issue", "Can publish publication issues"),
            ("set_current_publication_issue", "Can set the current publication issue"),
            ("rollback_publication_issue", "Can roll back publication issues"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.scope == PublicationIssueScope.MAIN_SITE and self.journal_id:
            errors["journal"] = "Main-site issues cannot be attached to a journal."
        if self.scope == PublicationIssueScope.JOURNAL and not self.journal_id:
            errors["journal"] = "Journal issues require a journal."
        if self.is_current and self.status != PublicationIssueStatus.PUBLISHED:
            errors["is_current"] = "Only a published issue can be current."
        if errors:
            raise ValidationError(errors)

    @property
    def scope_path(self):
        if self.scope == PublicationIssueScope.JOURNAL:
            return f"/journals/{self.journal.slug}/issues/{self.slug}/"
        return f"/issues/{self.slug}/"

    def __str__(self):
        scope = self.journal.name if self.journal_id else "Main site"
        return f"{scope}: {self.title}"


@register_snippet
class IssueArticle(models.Model):
    issue = models.ForeignKey(
        PublicationIssue,
        on_delete=models.CASCADE,
        related_name="issue_articles",
    )
    article = models.ForeignKey(
        "articles.ArticlePage",
        on_delete=models.PROTECT,
        related_name="issue_assignments",
    )
    section_label = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    panels = [
        FieldPanel("issue"),
        FieldPanel("article"),
        FieldPanel("section_label"),
        FieldPanel("sort_order"),
    ]

    class Meta:
        ordering = ["sort_order", "pk"]
        verbose_name = "issue article"
        verbose_name_plural = "issue articles"
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "article"],
                name="journals_issue_article_unique",
            )
        ]

    def clean(self):
        super().clean()
        if not self.article_id or not self.issue_id:
            return
        from ai_author_forum.articles.models import ArticlePage, ArticleReviewRecord

        errors = {}
        if self.article.review_status not in {
            ArticlePage.ReviewStatus.APPROVED,
            ArticlePage.ReviewStatus.PUBLISHED,
        }:
            errors["article"] = "Only reviewed articles can be added to an issue."
        elif (
            not self.article.approved_version_id
            or not ArticleReviewRecord.objects.filter(
                article=self.article,
                stage=ArticleReviewRecord.Stage.FINAL,
                action=ArticleReviewRecord.Action.FINAL_APPROVE,
                revision_id=self.article.approved_version_id,
            ).exists()
        ):
            errors["article"] = (
                "Article lacks a final approval for its approved revision."
            )
        if self.issue.scope == PublicationIssueScope.JOURNAL:
            journal_id = self.issue.journal_id
            if self.article.primary_journal_id != journal_id:
                errors["article"] = (
                    "A journal issue can only contain articles owned by that journal."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.issue}: {self.article}"


class JournalCategoryStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    HIDDEN = "hidden", "Hidden"
    DISABLED = "disabled", "Disabled"
    ARCHIVED = "archived", "Archived"


class JournalCategoryRedirectReason(models.TextChoices):
    MOVE = "move", "Move"
    SLUG_CHANGE = "slug_change", "Slug change"
    MANUAL_MIGRATION = "manual_migration", "Manual migration"


class JournalCategory(models.Model):
    MAX_DEPTH = 3
    SOFT_LIMIT_PER_JOURNAL = 30
    HARD_LIMIT_PER_JOURNAL = 100

    journal = models.ForeignKey(
        Journal,
        on_delete=models.PROTECT,
        related_name="categories",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=64)
    slug = models.SlugField(max_length=120)
    depth = models.PositiveSmallIntegerField(default=1, editable=False)
    path_cache = models.CharField(max_length=380, editable=False)
    description = models.TextField(blank=True)
    seo_title = models.CharField(max_length=255, blank=True)
    search_description = models.CharField(max_length=500, blank=True)
    cover_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    sort_order = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16,
        choices=JournalCategoryStatus.choices,
        default=JournalCategoryStatus.ACTIVE,
    )
    show_in_navigation = models.BooleanField(default=False)
    generate_static_page = models.BooleanField(default=True)
    aggregate_descendants = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_journal_categories",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_journal_categories",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("journal"),
                FieldPanel("parent"),
                FieldPanel("name"),
                FieldPanel("code"),
                FieldPanel("slug"),
                FieldPanel("sort_order"),
                FieldPanel("status"),
            ],
            heading="Category identity",
        ),
        MultiFieldPanel(
            [
                FieldPanel("description"),
                FieldPanel("seo_title"),
                FieldPanel("search_description"),
                FieldPanel("cover_image"),
                FieldPanel("show_in_navigation"),
                FieldPanel("generate_static_page"),
                FieldPanel("aggregate_descendants"),
            ],
            heading="Frontend settings",
        ),
        MultiFieldPanel(
            [
                FieldPanel("depth", read_only=True),
                FieldPanel("path_cache", read_only=True),
                FieldPanel("version", read_only=True),
                FieldPanel("created_at", read_only=True),
                FieldPanel("updated_at", read_only=True),
            ],
            heading="System state",
        ),
    ]

    class Meta:
        ordering = ["journal_id", "parent_id", "sort_order", "name", "pk"]
        indexes = [
            models.Index(fields=["journal", "status", "sort_order"]),
            models.Index(fields=["journal", "parent", "sort_order"]),
            models.Index(fields=["journal", "path_cache"]),
            models.Index(fields=["status", "show_in_navigation"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["journal", "code"], name="uniq_category_code_per_journal"
            ),
            models.UniqueConstraint(
                fields=["journal", "parent", "slug"],
                condition=Q(parent__isnull=False),
                name="uniq_category_child_slug",
            ),
            models.UniqueConstraint(
                fields=["journal", "slug"],
                condition=Q(parent__isnull=True),
                name="uniq_category_root_slug",
            ),
            models.UniqueConstraint(
                fields=["journal", "path_cache"],
                name="uniq_category_path_per_journal",
            ),
            models.CheckConstraint(
                condition=Q(depth__gte=1) & Q(depth__lte=3),
                name="category_depth_between_1_and_3",
            ),
        ]
        permissions = [
            ("move_journalcategory", "Can move journal categories"),
            ("change_category_status", "Can change journal category status"),
            ("archive_journalcategory", "Can archive journal categories"),
            ("migrate_category_references", "Can migrate category references"),
        ]
        verbose_name = "journal category"
        verbose_name_plural = "journal categories"

    def clean(self):
        super().clean()
        errors = {}
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                errors["parent"] = "A category cannot be its own parent."
            if self.parent.journal_id != self.journal_id:
                errors["parent"] = "Parent category must belong to the same journal."
        if self.depth < 1 or self.depth > self.MAX_DEPTH:
            errors["depth"] = "Category depth must be between 1 and 3."
        if self.show_in_navigation and (
            self.status != JournalCategoryStatus.ACTIVE or self.depth > 2
        ):
            errors["show_in_navigation"] = (
                "Only active first- or second-level categories may appear in navigation."
            )
        if (
            self.status
            in {
                JournalCategoryStatus.DISABLED,
                JournalCategoryStatus.ARCHIVED,
            }
            and self.generate_static_page
        ):
            errors["generate_static_page"] = (
                "Disabled or archived categories cannot generate static pages."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip()
        if self.slug:
            self.slug = self.slug.strip().strip("/")
        return super().save(*args, **kwargs)

    @property
    def full_path(self):
        return " > ".join(
            category.name for category in self.get_ancestors(include_self=True)
        )

    def get_ancestors(self, include_self=False):
        nodes = [self] if include_self else []
        current = self.parent
        seen = {self.pk} if self.pk else set()
        while current is not None and current.pk not in seen:
            nodes.append(current)
            seen.add(current.pk)
            current = current.parent
        return list(reversed(nodes))

    def get_descendant_ids(self, include_self=False):
        result = [self.pk] if include_self and self.pk else []
        frontier = [self.pk] if self.pk else []
        while frontier:
            children = list(
                type(self)
                .objects.filter(parent_id__in=frontier)
                .values_list("pk", flat=True)
            )
            result.extend(children)
            frontier = children
        return result

    def get_absolute_url(self):
        return f"/journals/{self.journal.slug}/categories/{self.path_cache}/"

    def get_static_output_path(self):
        return f"journals/{self.journal.slug}/categories/{self.path_cache}/index.html"

    def __str__(self):
        if (get_language() or "").lower().startswith("en"):
            return f"{self.journal}: {self.code or self.slug or self.name}"
        return f"{self.journal}: {self.path_cache or self.name}"


class JournalCategoryPathRedirect(models.Model):
    category = models.ForeignKey(
        JournalCategory,
        on_delete=models.PROTECT,
        related_name="path_redirects",
    )
    journal = models.ForeignKey(
        Journal,
        on_delete=models.PROTECT,
        related_name="category_path_redirects",
    )
    old_path = models.CharField(max_length=500)
    new_path = models.CharField(max_length=500)
    reason = models.CharField(
        max_length=24,
        choices=JournalCategoryRedirectReason.choices,
        default=JournalCategoryRedirectReason.MOVE,
    )
    http_status = models.PositiveSmallIntegerField(default=301)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_category_redirects",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["journal_id", "old_path", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["journal", "old_path"],
                condition=Q(is_active=True),
                name="uniq_active_category_old_path",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.category_id and self.journal_id != self.category.journal_id:
            errors["journal"] = "Redirect journal must match the category journal."
        for field in ("old_path", "new_path"):
            value = getattr(self, field)
            if value and (not value.startswith("/") or not value.endswith("/")):
                errors[field] = "Category paths must start and end with /."
        if self.old_path and self.new_path and self.old_path == self.new_path:
            errors["new_path"] = "A redirect cannot point to itself."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.old_path} -> {self.new_path}"


class StaticArticle(models.Model):
    """Compatibility source record; ArticlePage is the canonical article model."""

    journal = models.ForeignKey(
        Journal,
        on_delete=models.PROTECT,
        related_name="articles",
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    article_type = models.CharField(
        max_length=20,
        choices=ArticleType.choices,
        default=ArticleType.AI_ARTICLE,
    )
    authors = models.TextField(blank=True)
    ai_co_authors = models.TextField(blank=True)
    abstract = models.TextField(blank=True)
    keywords = models.TextField(blank=True)
    publication_date = models.DateTimeField(null=True, blank=True)
    cover_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Generated package paths include the package name and journal slug; keep
    # enough room for PostgreSQL to store the full storage key.
    html_source = models.FileField(
        upload_to="journal-articles/html/",
        blank=True,
        max_length=255,
    )
    source_html_path = models.CharField(max_length=255, blank=True)
    static_output_path = models.CharField(max_length=255, blank=True)
    review_status = models.CharField(
        max_length=20,
        choices=ArticleReviewStatus.choices,
        default=ArticleReviewStatus.DRAFT,
    )
    build_version = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    search_fields = [
        index.SearchField("title"),
        index.SearchField("slug"),
        index.SearchField("authors"),
        index.SearchField("abstract"),
        index.SearchField("keywords"),
    ]

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("journal"),
                FieldPanel("title"),
                FieldPanel("slug"),
                FieldPanel("article_type"),
                FieldPanel("review_status"),
                FieldPanel("sort_order"),
                FieldPanel("is_pinned"),
            ],
            heading="Article identity",
        ),
        MultiFieldPanel(
            [
                FieldPanel("authors"),
                FieldPanel("ai_co_authors"),
                FieldPanel("abstract"),
                FieldPanel("keywords"),
                FieldPanel("publication_date"),
                FieldPanel("cover_image"),
                FieldPanel("html_source"),
                FieldPanel("source_html_path"),
                FieldPanel("static_output_path", read_only=True),
                FieldPanel("build_version"),
                FieldPanel("notes"),
            ],
            heading="Content and delivery",
        ),
    ]

    class Meta:
        ordering = ["journal__sort_order", "sort_order", "title"]
        verbose_name = "static article"
        verbose_name_plural = "static articles"
        constraints = [
            models.UniqueConstraint(
                fields=["journal", "slug"],
                name="journals_staticarticle_journal_slug_unique",
            )
        ]

    def get_canonical_static_slug(self):
        try:
            canonical_page = self.canonical_page
        except (ObjectDoesNotExist, ValueError):
            canonical_page = None
        return getattr(canonical_page, "static_slug", "") or self.slug

    def get_absolute_url(self):
        return f"/articles/{self.get_canonical_static_slug()}/"

    def get_static_output_path(self):
        return f"{self.get_absolute_url()}index.html"

    def clean(self):
        super().clean()
        if self.title and not self.slug:
            self.slug = slugify(self.title, allow_unicode=True)
        if self.slug:
            self.static_output_path = self.get_static_output_path()

    def save(self, *args, **kwargs):
        if self.slug:
            self.static_output_path = self.get_static_output_path()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = tuple({*update_fields, "static_output_path"})
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} ({self.journal})"


class StaticArticleCategoryAssignment(models.Model):
    article = models.ForeignKey(
        StaticArticle,
        on_delete=models.CASCADE,
        related_name="category_assignments",
    )
    category = models.ForeignKey(
        JournalCategory,
        on_delete=models.PROTECT,
        related_name="static_article_assignments",
    )
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["article", "category"],
                name="uniq_static_article_category_assignment",
            ),
            models.UniqueConstraint(
                fields=["article"],
                condition=Q(is_primary=True),
                name="uniq_static_primary_category_per_article",
            ),
        ]

    def clean(self):
        super().clean()
        if self.article_id and self.category_id:
            if self.article.journal_id != self.category.journal_id:
                raise ValidationError(
                    {"category": "Category must belong to the article journal."}
                )
            if self.category.status not in {
                JournalCategoryStatus.ACTIVE,
                JournalCategoryStatus.HIDDEN,
            }:
                raise ValidationError({"category": "Category is not assignable."})

    def __str__(self):
        role = "primary" if self.is_primary else "related"
        return f"{self.article} -> {self.category} ({role})"


class ArticlePlacement(models.Model):
    article = models.ForeignKey(
        StaticArticle,
        on_delete=models.CASCADE,
        related_name="placements",
    )
    slot_code = models.CharField(max_length=80, db_index=True)
    slot_name = models.CharField(max_length=255, blank=True)
    scope = models.CharField(
        max_length=20,
        choices=LayoutScope.choices,
        default=LayoutScope.MAIN,
    )
    layout_type = models.CharField(
        max_length=20,
        choices=LayoutType.choices,
        default=LayoutType.LIST,
    )
    source_mode = models.CharField(
        max_length=20,
        choices=SourceMode.choices,
        default=SourceMode.MANUAL,
    )
    sort_order = models.PositiveIntegerField(default=0)
    pinned = models.BooleanField(default=False)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    display_title = models.CharField(max_length=255, blank=True)
    display_summary = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=PlacementStatus.choices,
        default=PlacementStatus.ACTIVE,
    )

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("article"),
                FieldPanel("slot_code"),
                FieldPanel("slot_name"),
                FieldPanel("scope"),
                FieldPanel("layout_type"),
                FieldPanel("source_mode"),
                FieldPanel("sort_order"),
                FieldPanel("pinned"),
                FieldPanel("start_at"),
                FieldPanel("end_at"),
                FieldPanel("display_title"),
                FieldPanel("display_summary"),
                FieldPanel("status"),
            ],
            heading="Placement",
        )
    ]

    class Meta:
        ordering = ["scope", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["article", "slot_code"],
                name="journals_articleplacement_article_slot_unique",
            )
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not getattr(
            self, "_allow_legacy_placement_save", False
        ):
            raise ValidationError(
                "journals.ArticlePlacement is retired and cannot accept new "
                "business writes. Use placements.ArticlePlacement instead."
            )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.slot_code} -> {self.article}"


class JournalAssetBinding(models.Model):
    journal = models.ForeignKey(
        Journal,
        on_delete=models.CASCADE,
        related_name="asset_bindings",
    )
    asset_type = models.CharField(
        max_length=20,
        choices=(
            ("cover", "Cover"),
            ("metrics", "Metrics"),
            ("article", "Article"),
            ("hero", "Hero"),
        ),
    )
    image = models.ForeignKey(
        CustomImage,
        on_delete=models.CASCADE,
        related_name="+",
    )
    used_by = models.CharField(max_length=255, blank=True)
    source_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AssetBindingStatus.choices,
        default=AssetBindingStatus.BOUND,
    )
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("journal"),
                FieldPanel("asset_type"),
                FieldPanel("image"),
                FieldPanel("used_by"),
                FieldPanel("source_name"),
                FieldPanel("status"),
                FieldPanel("sort_order"),
                FieldPanel("notes"),
            ],
            heading="Binding",
        )
    ]

    class Meta:
        ordering = ["journal__sort_order", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["journal", "asset_type", "source_name"],
                name="journals_assetbinding_unique",
            )
        ]

    def __str__(self):
        return f"{self.journal} / {self.asset_type}"


def _job_status_field(default=ImportJobStatus.PENDING):
    return models.CharField(
        max_length=20, choices=ImportJobStatus.choices, default=default
    )


class BaseImportJob(models.Model):
    source_file = models.FileField(upload_to=import_upload_to)
    package_name = models.CharField(max_length=255, blank=True)
    status = _job_status_field()
    total_rows = models.PositiveIntegerField(default=0)
    success_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    error_report = models.FileField(upload_to=import_upload_to, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class JournalImportJob(BaseImportJob):
    class Meta:
        verbose_name = "journal import job"
        verbose_name_plural = "journal import jobs"


class ArticleImportJob(BaseImportJob):
    import_scope = models.CharField(
        max_length=16,
        choices=ArticleImportScope.choices,
        default=ArticleImportScope.GLOBAL,
        db_index=True,
    )
    target_journal = models.ForeignKey(
        Journal,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="article_import_jobs",
    )
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_format = models.CharField(max_length=16, blank=True, db_index=True)
    parser_version = models.CharField(max_length=64, blank=True)
    template_version = models.PositiveIntegerField(default=1)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_article_import_jobs",
    )

    class Meta:
        verbose_name = "article import job"
        verbose_name_plural = "article import jobs"


class BaseImportRow(models.Model):
    row_no = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ImportRowStatus.choices,
        default=ImportRowStatus.SUCCESS,
    )
    error_message = models.TextField(blank=True)
    action = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class JournalImportRow(BaseImportRow):
    job = models.ForeignKey(
        JournalImportJob,
        on_delete=models.CASCADE,
        related_name="rows",
    )
    journal = models.ForeignKey(
        Journal,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["row_no", "id"]
        verbose_name = "journal import row"
        verbose_name_plural = "journal import rows"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "row_no"], name="journals_journalimportrow_unique"
            )
        ]


class ArticleImportRow(BaseImportRow):
    job = models.ForeignKey(
        ArticleImportJob,
        on_delete=models.CASCADE,
        related_name="rows",
    )
    article = models.ForeignKey(
        StaticArticle,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    article_page = models.ForeignKey(
        "articles.ArticlePage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_rows",
    )
    error_code = models.CharField(max_length=64, blank=True)
    error_field = models.CharField(max_length=64, blank=True)
    source_path = models.CharField(max_length=500, blank=True)
    source_format = models.CharField(max_length=16, blank=True)
    conversion_warnings = models.JSONField(default=list, blank=True)
    normalized_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["row_no", "id"]
        verbose_name = "article import row"
        verbose_name_plural = "article import rows"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "row_no"], name="journals_articleimportrow_unique"
            )
        ]
