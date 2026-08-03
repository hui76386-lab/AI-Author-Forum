from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField
from wagtail.snippets.models import register_snippet

from ai_author_forum.journals.validators import (
    validate_controlled_rich_text,
    validate_public_link,
)


@register_setting(icon="cog")
class SiteSettings(BaseSiteSetting):
    site_name = models.CharField(
        max_length=120,
        default="AI Author Forum",
        verbose_name="主站名称",
    )
    logo = models.ForeignKey(
        "images.CustomImage",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="主站 Logo",
    )
    seo_title = models.CharField(
        max_length=160,
        blank=True,
        verbose_name="默认 SEO 标题",
    )
    seo_description = models.TextField(
        blank=True,
        verbose_name="默认 SEO 描述",
    )
    default_image = models.ForeignKey(
        "images.CustomImage",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="默认图片",
    )
    static_output_root = models.CharField(
        max_length=500,
        default="published",
        verbose_name="静态输出根目录",
        help_text="相对路径会以项目根目录为基准；生产环境建议通过 STATIC_PUBLISH_ROOT 配置。",
    )
    core_navigation_locked = models.BooleanField(
        default=True,
        verbose_name="锁定核心导航",
        help_text="开启后，核心导航只能由具备核心导航权限的管理员维护。",
    )

    panels = [
        FieldPanel("site_name"),
        FieldPanel("logo"),
        MultiFieldPanel(
            [FieldPanel("seo_title"), FieldPanel("seo_description")],
            heading="默认 SEO",
        ),
        FieldPanel("default_image"),
        FieldPanel("static_output_root"),
        FieldPanel("core_navigation_locked"),
    ]

    class Meta:
        verbose_name = "主站配置"


class NavigationScope(models.TextChoices):
    MAIN_SITE = "main_site", "Main site"
    JOURNAL = "journal", "Journal"


class NavigationSetStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class NavigationTargetType(models.TextChoices):
    CONTENT_COLUMN = "content_column", "Content column"
    WAGTAIL_PAGE = "wagtail_page", "Wagtail page"
    CURRENT_ISSUE = "current_issue", "Current issue"
    ISSUE_ARCHIVE = "issue_archive", "Issue archive"
    INTERNAL_PATH = "internal_path", "Internal path"
    EXTERNAL_URL = "external_url", "External URL"
    GROUP_ONLY = "group_only", "Group only"


class NavigationEntryStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    HIDDEN = "hidden", "Hidden"
    ARCHIVED = "archived", "Archived"


@register_snippet
class NavigationSet(models.Model):
    MAX_GROUPS = 8

    scope = models.CharField(
        max_length=20,
        choices=NavigationScope.choices,
        default=NavigationScope.MAIN_SITE,
    )
    site = models.ForeignKey(
        "wagtailcore.Site",
        on_delete=models.CASCADE,
        related_name="managed_navigation_sets",
    )
    journal = models.ForeignKey(
        "journals.Journal",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="navigation_sets",
    )
    name = models.CharField(max_length=120)
    status = models.CharField(
        max_length=16,
        choices=NavigationSetStatus.choices,
        default=NavigationSetStatus.DRAFT,
    )
    version = models.PositiveIntegerField(default=1)
    is_template = models.BooleanField(
        default=False,
        help_text="Use only for default journal navigation templates; never as a real journal scope.",
    )
    copied_from_template = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copied_navigation_sets",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_navigation_sets",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("scope"),
                FieldPanel("site"),
                FieldPanel("journal"),
                FieldPanel("name"),
                FieldPanel("status"),
                FieldPanel("is_template"),
                FieldPanel("copied_from_template"),
            ],
            heading="Navigation set",
        ),
        FieldPanel("version", read_only=True),
    ]

    class Meta:
        ordering = ["scope", "journal_id", "site_id", "name", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        Q(scope="main_site")
                        & Q(journal__isnull=True)
                        & Q(is_template=False)
                    )
                    | (
                        Q(scope="journal")
                        & Q(journal__isnull=False)
                        & Q(is_template=False)
                    )
                    | (Q(is_template=True) & Q(journal__isnull=True))
                ),
                name="managed_navigation_scope_consistency",
            ),
            models.UniqueConstraint(
                fields=["site"],
                condition=Q(scope="main_site", status="active", is_template=False),
                name="one_active_main_navigation_set_per_site",
            ),
            models.UniqueConstraint(
                fields=["journal"],
                condition=Q(scope="journal", status="active", is_template=False),
                name="one_active_navigation_set_per_journal",
            ),
            models.UniqueConstraint(
                fields=["site", "name"],
                condition=Q(is_template=True),
                name="unique_navigation_template_name_per_site",
            ),
        ]
        permissions = [
            ("view_main_navigation", "Can view main site navigation"),
            ("view_journal_navigation", "Can view journal navigation"),
            (
                "view_navigation_template",
                "Can view default journal navigation templates",
            ),
            ("manage_main_navigation", "Can manage main site navigation"),
            ("manage_journal_navigation", "Can manage journal navigation"),
            (
                "manage_navigation_template",
                "Can manage default journal navigation templates",
            ),
            ("publish_navigation_changes", "Can publish navigation changes"),
            ("delete_navigation_objects", "Can hard delete navigation objects"),
        ]
        verbose_name = "navigation set"
        verbose_name_plural = "navigation sets"

    def clean(self):
        super().clean()
        errors = {}
        if self.is_template:
            if self.journal_id:
                errors["journal"] = (
                    "Default navigation templates cannot be bound to a real journal."
                )
        elif self.scope == NavigationScope.MAIN_SITE and self.journal_id:
            errors["journal"] = "Main site navigation must not be bound to a journal."
        elif self.scope == NavigationScope.JOURNAL and not self.journal_id:
            errors["journal"] = "Journal navigation must be bound to one journal."
        if errors:
            raise ValidationError(errors)

    @property
    def is_main_site(self):
        return self.scope == NavigationScope.MAIN_SITE and not self.is_template

    @property
    def is_journal(self):
        return self.scope == NavigationScope.JOURNAL and not self.is_template

    def bump_version(self, *, user=None, save=True):
        self.version = (self.version or 0) + 1
        if user is not None and getattr(user, "is_authenticated", False):
            self.updated_by = user
        if save:
            self.save(update_fields=["version", "updated_by", "updated_at"])
        return self.version

    def __str__(self):
        if self.is_template:
            return f"Template: {self.name}"
        if self.journal_id:
            return f"{self.journal}: {self.name}"
        return self.name


@register_snippet
class NavigationGroup(models.Model):
    MAX_ITEMS = 20

    navigation_set = models.ForeignKey(
        NavigationSet,
        on_delete=models.CASCADE,
        related_name="groups",
    )
    label = models.CharField(max_length=120)
    code = models.SlugField(max_length=120)
    sort_order = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=NavigationEntryStatus.choices,
        default=NavigationEntryStatus.ACTIVE,
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    panels = [
        FieldPanel("navigation_set"),
        FieldPanel("label"),
        FieldPanel("code"),
        FieldPanel("sort_order"),
        FieldPanel("is_visible"),
        FieldPanel("status"),
        FieldPanel("version", read_only=True),
    ]

    class Meta:
        ordering = ["navigation_set", "sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["navigation_set", "code"],
                name="unique_navigation_group_code_per_set",
            )
        ]
        permissions = [
            ("archive_navigationgroup", "Can archive navigation groups"),
            ("move_navigationgroup", "Can move navigation groups"),
        ]
        verbose_name = "navigation group"
        verbose_name_plural = "navigation groups"

    def clean(self):
        super().clean()

    def bump_version(self, *, save=True):
        self.version = (self.version or 0) + 1
        if save:
            self.save(update_fields=["version", "updated_at"])
            self.navigation_set.bump_version()
        return self.version

    def __str__(self):
        return f"{self.navigation_set} / {self.label}"


class NavigationArea(models.TextChoices):
    HOME = "home", "首页"
    JOURNALS = "journals", "子期刊"
    ARTICLES = "articles", "文章"
    ABOUT = "about", "关于"


class NavigationItem(models.Model):
    site = models.ForeignKey(
        "wagtailcore.Site",
        on_delete=models.CASCADE,
        related_name="navigation_items",
        verbose_name="站点",
    )
    area = models.CharField(
        max_length=24,
        choices=NavigationArea.choices,
        verbose_name="导航区域",
    )
    label = models.CharField(max_length=120, verbose_name="显示名称")
    slug = models.SlugField(max_length=120, verbose_name="唯一标识")
    parent = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="children",
        verbose_name="父级菜单",
    )
    group = models.ForeignKey(
        "site_settings.NavigationGroup",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="items",
        help_text="Managed two-level navigation group. Leave empty only for legacy baseline navigation.",
    )
    code = models.SlugField(
        max_length=120,
        blank=True,
        help_text="Unique item code inside one navigation set. Defaults to the legacy slug when empty.",
    )
    page = models.ForeignKey(
        "wagtailcore.Page",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="navigation_items",
        verbose_name="关联页面",
    )
    url = models.URLField(blank=True, verbose_name="外部链接")
    target_type = models.CharField(
        max_length=24,
        choices=NavigationTargetType.choices,
        default=NavigationTargetType.INTERNAL_PATH,
        help_text="Required for managed navigation items.",
    )
    category = models.ForeignKey(
        "journals.JournalCategory",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="navigation_items",
    )
    internal_path = models.CharField(
        max_length=500,
        blank=True,
        validators=[validate_public_link],
        help_text="Use a safe site-relative path such as /journals/ or /sections/news/.",
    )
    external_url = models.URLField(
        blank=True,
        validators=[validate_public_link],
        help_text="External links must use https://.",
    )
    open_in_new_tab = models.BooleanField(default=False)
    is_visible = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=NavigationEntryStatus.choices,
        default=NavigationEntryStatus.ACTIVE,
    )
    allow_direct_access = models.BooleanField(
        default=True,
        help_text="When hidden, controls whether the generated column page remains directly accessible.",
    )
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_navigation_items",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序")
    is_active = models.BooleanField(default=True, verbose_name="启用")
    is_core = models.BooleanField(
        default=True,
        verbose_name="核心导航",
        help_text="核心导航的变更应由系统管理员执行。",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    panels = [
        FieldPanel("site"),
        FieldPanel("area"),
        FieldPanel("label"),
        FieldPanel("slug"),
        FieldPanel("parent"),
        FieldPanel("group"),
        FieldPanel("code"),
        FieldPanel("target_type"),
        FieldPanel("category"),
        FieldPanel("page"),
        FieldPanel("internal_path"),
        FieldPanel("external_url"),
        FieldPanel("url"),
        FieldPanel("open_in_new_tab"),
        FieldPanel("sort_order"),
        FieldPanel("is_active"),
        FieldPanel("is_visible"),
        FieldPanel("status"),
        FieldPanel("allow_direct_access"),
        FieldPanel("is_core"),
    ]

    class Meta:
        ordering = ["site", "area", "sort_order", "pk"]
        verbose_name = "导航项"
        verbose_name_plural = "导航基线"
        constraints = [
            models.UniqueConstraint(
                fields=["site", "area", "slug"],
                condition=Q(group__isnull=True),
                name="legacy_navigation_site_area_slug_uniq",
            )
        ]
        permissions = [
            ("manage_core_navigation", "Can manage core navigation"),
            ("archive_navigationitem", "Can archive navigation items"),
            ("move_navigationitem", "Can move navigation items"),
        ]

    def __str__(self) -> str:
        return f"{self.get_area_display()} / {self.label}"

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.parent_id and self.parent_id == self.pk:
            errors["parent"] = "Navigation items cannot be their own parent."
        if self.page_id and self.url and not self.group_id:
            errors["page"] = "Legacy page and legacy URL can only choose one."
        if self.group_id:
            if self.parent_id:
                errors["parent"] = (
                    "Managed navigation is limited to two levels: group -> item."
                )
            code = self.code or self.slug
            if not code:
                errors["code"] = "Managed navigation items require a code."
            elif self.group_id:
                duplicate = (
                    type(self)
                    .objects.filter(
                        group__navigation_set_id=self.group.navigation_set_id,
                        code=code,
                    )
                    .exclude(pk=self.pk)
                )
                if duplicate.exists():
                    errors["code"] = (
                        "Item code must be unique inside the navigation set."
                    )
            target_fields = {
                NavigationTargetType.CONTENT_COLUMN: {"category"},
                NavigationTargetType.WAGTAIL_PAGE: {"page"},
                NavigationTargetType.INTERNAL_PATH: {"internal_path"},
                NavigationTargetType.EXTERNAL_URL: {"external_url"},
                NavigationTargetType.CURRENT_ISSUE: set(),
                NavigationTargetType.ISSUE_ARCHIVE: set(),
                NavigationTargetType.GROUP_ONLY: set(),
            }
            configured = {
                "category": bool(self.category_id),
                "page": bool(self.page_id),
                "internal_path": bool(self.internal_path),
                "external_url": bool(self.external_url),
            }
            allowed = target_fields.get(self.target_type, set())
            if self.target_type not in target_fields:
                errors["target_type"] = "Unsupported navigation target type."
            for field, has_value in configured.items():
                if has_value and field not in allowed:
                    errors[field] = (
                        "This target field does not match the selected target type."
                    )
            if (
                self.target_type == NavigationTargetType.WAGTAIL_PAGE
                and not self.page_id
            ):
                errors["page"] = "Wagtail page targets must choose one page."
            if (
                self.target_type == NavigationTargetType.INTERNAL_PATH
                and not self.internal_path
            ):
                errors["internal_path"] = (
                    "Internal path targets must provide a site-relative path."
                )
            if (
                self.target_type == NavigationTargetType.EXTERNAL_URL
                and not self.external_url
            ):
                errors["external_url"] = (
                    "External URL targets must provide an https:// URL."
                )
            if (
                self.target_type == NavigationTargetType.EXTERNAL_URL
                and self.external_url
                and not self.external_url.startswith("https://")
            ):
                errors["external_url"] = "External URL targets must use https://."
            if (
                self.target_type == NavigationTargetType.INTERNAL_PATH
                and self.internal_path
            ):
                if not self.internal_path.startswith(
                    "/"
                ) or self.internal_path.startswith("//"):
                    errors["internal_path"] = (
                        "Internal paths must start with one slash."
                    )
            if self.category_id and self.group_id:
                nav_set = self.group.navigation_set
                if (
                    nav_set.journal_id
                    and self.category.journal_id != nav_set.journal_id
                ):
                    errors["category"] = (
                        "Category must belong to the same journal navigation set."
                    )
                if (
                    nav_set.is_main_site
                    and getattr(self.category.journal, "status", None) != "active"
                ):
                    errors["category"] = (
                        "Main-site content entries cannot point to an inactive journal category."
                    )
            if self.status == NavigationEntryStatus.ARCHIVED and self.is_visible:
                errors["is_visible"] = "Archived navigation items cannot be visible."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = (
                type(self)
                .objects.select_related("group__navigation_set__journal")
                .filter(pk=self.pk)
                .first()
            )
        if not self.code and self.slug:
            self.code = self.slug
        if not self.slug and self.code:
            self.slug = self.code
        result = super().save(*args, **kwargs)
        if self.group_id:
            NavigationSet.objects.filter(pk=self.group.navigation_set_id).update(
                version=models.F("version") + 1,
                updated_at=timezone.now(),
                updated_by_id=self.updated_by_id,
            )
            old_url = previous.target_url if previous else ""
            new_url = self.target_url
            if (
                previous
                and previous.target_type == NavigationTargetType.CONTENT_COLUMN
                and self.target_type == NavigationTargetType.CONTENT_COLUMN
                and old_url
                and new_url
                and old_url != new_url
            ):
                NavigationItemPathRedirect.objects.update_or_create(
                    old_path=old_url,
                    defaults={
                        "navigation_item": self,
                        "new_path": new_url,
                        "http_status": 301,
                        "is_active": True,
                    },
                )
                from .navigation import navigation_audit_metadata

                nav_set = self.group.navigation_set
                nav_set.refresh_from_db(fields=["version"])
                AuditLog.record(
                    action=AuditAction.CONFIGURE,
                    status=AuditStatus.SUCCESS,
                    actor=self.updated_by,
                    target=self,
                    message="Navigation item slug changed; permanent redirect recorded.",
                    metadata=navigation_audit_metadata(
                        nav_set,
                        before={"path": old_url},
                        after={"path": new_url},
                        old_path=old_url,
                        new_path=new_url,
                    ),
                )
        return result

    @property
    def managed_code(self) -> str:
        return self.code or self.slug

    @property
    def placement_target_slug(self) -> str:
        if not self.group_id:
            return self.slug
        nav_set = self.group.navigation_set
        code = self.managed_code
        if nav_set.journal_id:
            return f"{nav_set.journal.slug}--{code}"
        return code

    @property
    def target_url(self) -> str:
        if self.group_id:
            nav_set = self.group.navigation_set
            code = self.managed_code
            if self.target_type == NavigationTargetType.CONTENT_COLUMN:
                if nav_set.journal_id:
                    return f"/journals/{nav_set.journal.slug}/sections/{code}/"
                return f"/sections/{code}/"
            if self.target_type == NavigationTargetType.WAGTAIL_PAGE and self.page_id:
                return self.page.url
            if self.target_type == NavigationTargetType.CURRENT_ISSUE:
                if nav_set.journal_id:
                    return f"/journals/{nav_set.journal.slug}/current-issue/"
                return "/explore-content/current-issue/"
            if self.target_type == NavigationTargetType.ISSUE_ARCHIVE:
                if nav_set.journal_id:
                    return f"/journals/{nav_set.journal.slug}/issues/"
                return "/explore-content/browse-issues/"
            if self.target_type == NavigationTargetType.INTERNAL_PATH:
                return self.internal_path
            if self.target_type == NavigationTargetType.EXTERNAL_URL:
                return self.external_url
            return ""
        if self.page_id:
            return self.page.url
        return self.url


class NavigationItemPathRedirect(models.Model):
    navigation_item = models.ForeignKey(
        NavigationItem,
        on_delete=models.PROTECT,
        related_name="path_redirects",
    )
    old_path = models.CharField(max_length=500, unique=True)
    new_path = models.CharField(max_length=500)
    http_status = models.PositiveSmallIntegerField(default=301)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["old_path", "pk"]
        verbose_name = "navigation path redirect"
        verbose_name_plural = "navigation path redirects"

    def clean(self):
        super().clean()
        errors = {}
        for field_name in ("old_path", "new_path"):
            value = getattr(self, field_name, "")
            if not value.startswith("/") or value.startswith("//"):
                errors[field_name] = (
                    "Navigation redirect paths must start with one slash."
                )
        if self.old_path == self.new_path:
            errors["new_path"] = "Redirect target must differ from the old path."
        if self.http_status not in {301, 308}:
            errors["http_status"] = (
                "Only permanent redirect status 301 or 308 is supported."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.old_path} -> {self.new_path}"


class ColumnTemplateVariant(models.TextChoices):
    RESEARCH_LIST = "research_list", "Research list"
    NEWS_LANDING = "news_landing", "News landing"
    CHRONOLOGICAL = "chronological", "Chronological list"


class ColumnDefaultSort(models.TextChoices):
    PUBLISHED_DESC = "published_desc", "Publication date (newest first)"


class ColumnEmptyBehavior(models.TextChoices):
    BLOCK_PUBLISH = "block_publish", "Block production publish"
    HIDE_NAVIGATION = "hide_navigation", "Hide navigation"
    EDITORIAL_MESSAGE = "editorial_message", "Show editorial message"


@register_snippet
class ContentColumnConfig(models.Model):
    navigation_item = models.OneToOneField(
        NavigationItem,
        on_delete=models.CASCADE,
        related_name="content_column_config",
    )
    intro = RichTextField(
        blank=True,
        features=["h2", "h3", "bold", "italic", "ol", "ul", "link", "blockquote"],
        validators=[validate_controlled_rich_text],
    )
    cover_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="content_column_configs",
    )
    category = models.ForeignKey(
        "journals.JournalCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="content_column_configs",
    )
    template_variant = models.CharField(
        max_length=32,
        choices=ColumnTemplateVariant.choices,
        default=ColumnTemplateVariant.CHRONOLOGICAL,
    )
    default_sort = models.CharField(
        max_length=32,
        choices=ColumnDefaultSort.choices,
        default=ColumnDefaultSort.PUBLISHED_DESC,
    )
    minimum_publish_items = models.PositiveSmallIntegerField(default=1)
    empty_behavior = models.CharField(
        max_length=32,
        choices=ColumnEmptyBehavior.choices,
        default=ColumnEmptyBehavior.BLOCK_PUBLISH,
    )
    show_open_access_badge = models.BooleanField(default=False)
    show_authors = models.BooleanField(default=True)
    show_abstract = models.BooleanField(default=True)
    enable_type_filter = models.BooleanField(default=True)
    enable_year_filter = models.BooleanField(default=True)
    page_size = models.PositiveSmallIntegerField(default=20)
    seo_title = models.CharField(max_length=160, blank=True)
    seo_description = models.TextField(blank=True)
    empty_message = models.CharField(
        max_length=255,
        default="No placed articles are currently available in this column.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    panels = [
        FieldPanel("navigation_item"),
        FieldPanel("intro"),
        FieldPanel("cover_image"),
        FieldPanel("category"),
        FieldPanel("template_variant"),
        FieldPanel("default_sort"),
        FieldPanel("minimum_publish_items"),
        FieldPanel("empty_behavior"),
        FieldPanel("show_open_access_badge"),
        FieldPanel("show_authors"),
        FieldPanel("show_abstract"),
        FieldPanel("enable_type_filter"),
        FieldPanel("enable_year_filter"),
        FieldPanel("page_size"),
        FieldPanel("seo_title"),
        FieldPanel("seo_description"),
        FieldPanel("empty_message"),
    ]

    class Meta:
        verbose_name = "content column configuration"
        verbose_name_plural = "content column configurations"

    def clean(self):
        super().clean()
        errors = {}
        item = self.navigation_item
        if item and item.target_type != NavigationTargetType.CONTENT_COLUMN:
            errors["navigation_item"] = (
                "Content column config can only attach to content-column navigation items."
            )
        if item and item.group_id and self.category_id:
            nav_set = item.group.navigation_set
            if nav_set.journal_id and self.category.journal_id != nav_set.journal_id:
                errors["category"] = (
                    "Content column category must belong to the same journal."
                )
        if self.page_size < 1:
            errors["page_size"] = "Page size must be at least 1."
        if self.minimum_publish_items < 1:
            errors["minimum_publish_items"] = (
                "Minimum publish items must be at least 1."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Content column: {self.navigation_item}"


class AdminRolePreset(models.Model):
    role_code = models.SlugField(max_length=64, unique=True, verbose_name="角色编码")
    display_name = models.CharField(max_length=120, verbose_name="角色名称")
    description = models.TextField(blank=True, verbose_name="角色说明")
    group = models.OneToOneField(
        "auth.Group",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="admin_role_preset",
        verbose_name="Wagtail 用户组",
    )
    is_active = models.BooleanField(default=True, verbose_name="启用")
    is_system = models.BooleanField(default=True, verbose_name="系统预设")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    panels = [
        FieldPanel("role_code"),
        FieldPanel("display_name"),
        FieldPanel("description"),
        FieldPanel("group"),
        FieldPanel("is_active"),
        FieldPanel("is_system"),
    ]

    class Meta:
        ordering = ["role_code"]
        verbose_name = "后台角色预设"
        verbose_name_plural = "后台角色预设"
        permissions = [
            ("access_journals", "Can access journals module"),
            ("access_articles", "Can access articles module"),
            ("access_article_review", "Can access article review module"),
            ("access_placements", "Can access placements module"),
            ("access_slots", "Can access layout slots module"),
            ("access_static_publish", "Can access static publishing module"),
            ("access_site_settings", "Can access site settings module"),
            ("access_audit_log", "Can access audit log module"),
            ("review_articles", "Can review articles"),
            ("import_journals", "Can import journals"),
            ("import_articles", "Can import articles"),
        ]

    def __str__(self) -> str:
        return self.display_name


class AuditAction(models.TextChoices):
    IMPORT = "import", "导入"
    PUBLISH = "publish", "发布"
    ROLLBACK = "rollback", "回滚"
    RETRY = "retry", "重试"
    CONFIGURE = "configure", "配置变更"
    PERMISSION = "permission", "权限变更"


class AuditStatus(models.TextChoices):
    SUCCESS = "success", "成功"
    FAILURE = "failure", "失败"
    STARTED = "started", "进行中"


class AuditLog(models.Model):
    action = models.CharField(
        max_length=32,
        choices=AuditAction.choices,
        db_index=True,
        verbose_name="动作",
    )
    status = models.CharField(
        max_length=16,
        choices=AuditStatus.choices,
        db_index=True,
        verbose_name="状态",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="ai_author_forum_audit_logs",
        verbose_name="操作者",
    )
    target_type = models.CharField(max_length=120, blank=True, verbose_name="目标类型")
    target_id = models.CharField(max_length=120, blank=True, verbose_name="目标 ID")
    target_label = models.CharField(max_length=255, blank=True, verbose_name="目标名称")
    message = models.TextField(blank=True, verbose_name="说明")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="附加数据")
    request_id = models.CharField(max_length=120, blank=True, verbose_name="请求 ID")
    ip_address = models.GenericIPAddressField(
        blank=True, null=True, verbose_name="IP 地址"
    )
    created_at = models.DateTimeField(
        default=timezone.now, db_index=True, verbose_name="发生时间"
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "审计日志"
        verbose_name_plural = "审计日志"
        permissions = [
            ("export_audit_log", "Can export audit logs"),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} / {self.target_label or self.target_type}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("审计日志创建后不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("审计日志创建后不可删除。")

    @classmethod
    def record(
        cls,
        *,
        action: str,
        status: str,
        actor=None,
        target=None,
        target_type: str = "",
        target_id: str = "",
        target_label: str = "",
        message: str = "",
        metadata: dict | None = None,
        request_id: str = "",
        ip_address: str | None = None,
    ) -> AuditLog:
        if target is not None:
            target_type = target_type or target.__class__.__name__
            target_id = target_id or str(getattr(target, "pk", ""))
            target_label = target_label or str(target)
        return cls.objects.create(
            action=action,
            status=status,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            message=message,
            metadata=metadata or {},
            request_id=request_id,
            ip_address=ip_address,
        )
