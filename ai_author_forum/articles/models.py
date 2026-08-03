from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.http import HttpResponsePermanentRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.text import slugify
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import (
    FieldPanel,
    HelpPanel,
    MultiFieldPanel,
    ObjectList,
    TabbedInterface,
)
from wagtail.fields import StreamField
from wagtail.models import AbstractGroupApprovalTask, Orderable, Page, TaskState
from wagtail.search import index

from ai_author_forum.utils.i18n import article_type_label

from .blocks import ArticleBodyBlock
from .forms import ArticleCategoryAssignmentInlinePanel, ArticlePageForm
from .integrations import get_article_fallback_context, log_article_audit
from .panels import PreviewButton

ARTICLE_REVIEW_PERMISSION = "articles.review_article"
ARTICLE_EDIT_PERMISSION = "articles.edit_article"
ARTICLE_PLACEMENT_PERMISSION = "articles.trigger_article_placement"
ARTICLE_RAW_HTML_PERMISSION = "articles.use_raw_html"
ARTICLE_PAGE_TEMPLATE = "articles/article_page.html"
ARTICLE_PREVIEW_TEMPLATE = ARTICLE_PAGE_TEMPLATE
WAGTAIL_ADMIN_ACCESS_PERMISSION = "wagtailadmin.access_admin"


def user_has_article_permission(user, permission_name):
    if user is None:
        return False

    if not user.is_active:
        return False

    if user.is_superuser:
        return True

    return user.has_perm(WAGTAIL_ADMIN_ACCESS_PERMISSION) and user.has_perm(
        permission_name
    )


def user_has_article_edit_permission(user):
    return user_has_article_permission(user, ARTICLE_EDIT_PERMISSION)


def user_has_article_review_permission(user):
    return user_has_article_permission(user, ARTICLE_REVIEW_PERMISSION)


def user_has_article_placement_permission(user):
    return user_has_article_permission(user, ARTICLE_PLACEMENT_PERMISSION)


def user_has_raw_html_permission(user):
    return user_has_article_permission(user, ARTICLE_RAW_HTML_PERMISSION)


class ArticleRevisionConflict(ValidationError):
    pass


class ArticlePage(Page):
    template = ARTICLE_PAGE_TEMPLATE
    page_ptr = models.OneToOneField(
        Page,
        on_delete=models.CASCADE,
        parent_link=True,
        related_name="articles_articlepage",
    )
    base_form_class = ArticlePageForm

    class ArticleType(models.TextChoices):
        AI_ARTICLE = "AI Article", "AI 文章"
        NEWS = "News", "新闻"
        OPINION = "Opinion", "观点"
        RESEARCH_ANALYSIS = "Research Analysis", "研究分析"

    class ReviewStatus(models.TextChoices):
        DRAFT = "draft", "草稿"
        SUBMITTED = "submitted", "待审核"
        APPROVED = "approved", "审核通过"
        REJECTED = "rejected", "已驳回"
        # Compatibility alias for records created before publication state was split out.
        PUBLISHED = "published", "已发布（兼容状态）"

    class PublicationStatus(models.TextChoices):
        APPROVED = "approved", "已通过，待投放"
        PLACED = "placed", "已投放"
        BUILT = "built", "静态 HTML 已构建"
        PUBLISHED = "published", "静态版本已发布"
        OFFLINE = "offline", "已下线"

    class PlacementSyncStatus(models.TextChoices):
        PENDING = "pending", "待同步"
        SYNCED = "synced", "已同步"
        FAILED = "failed", "同步失败"

    abstract = models.TextField()
    featured_image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="文章封面",
    )
    featured_image_alt = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="封面替代文本",
        help_text="用于无障碍访问；留空时依次使用图片标题和文章标题。",
    )
    body = StreamField(
        ArticleBodyBlock(),
        min_num=1,
        verbose_name="正文",
        help_text=(
            "默认使用可视化正文段落，也可按需插入章节标题、图片、引用、列表、"
            "表格和附件；无需手写 HTML。"
        ),
    )
    authors = models.CharField(
        max_length=255,
        help_text="多个作者请使用英文逗号分隔。",
    )
    ai_co_authors = models.CharField(max_length=255, blank=True)
    ai_contribution_statement = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    article_type = models.CharField(
        max_length=32,
        choices=ArticleType.choices,
        default=ArticleType.AI_ARTICLE,
    )
    source_static_article = models.OneToOneField(
        "journals.StaticArticle",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="canonical_page",
        editable=False,
    )
    primary_journal = models.ForeignKey(
        "journals.Journal",
        on_delete=models.PROTECT,
        related_name="primary_articles",
    )
    related_journals = models.ManyToManyField(
        "journals.Journal",
        blank=True,
        related_name="related_articles",
    )
    keywords = models.CharField(
        max_length=255,
        help_text="多个关键词请使用英文逗号分隔。",
    )
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.DRAFT,
    )
    publication_status = models.CharField(
        max_length=16,
        choices=PublicationStatus.choices,
        blank=True,
        default="",
        db_index=True,
        editable=False,
        help_text=("静态交付状态；文章审核通过与前台发布分别记录。"),
    )
    build_version = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        editable=False,
        help_text="最近一次成功生成该文章的静态发布版本。",
    )
    published_version = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        editable=False,
        help_text="当前包含该文章的活动静态发布版本。",
    )
    publish_failure_reason = models.TextField(blank=True, editable=False)
    placement_sync_status = models.CharField(
        max_length=12,
        choices=PlacementSyncStatus.choices,
        default=PlacementSyncStatus.PENDING,
        editable=False,
    )
    placement_sync_error = models.TextField(blank=True, editable=False)
    placement_synced_revision_id = models.PositiveBigIntegerField(
        null=True, blank=True, editable=False
    )
    placement_sync_request_id = models.CharField(
        max_length=64, blank=True, db_index=True, editable=False
    )
    last_built_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_static_published_at = models.DateTimeField(
        null=True, blank=True, editable=False
    )
    static_slug = models.CharField(
        max_length=255,
        blank=True,
        unique=True,
        db_index=True,
        help_text=("静态 HTML 输出路径片段；留空时根据文章标题自动生成。"),
    )
    # Page already records revisions; these fields point to the decision version.
    approved_version = models.ForeignKey(
        "wagtailcore.Revision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        help_text="文章审核通过时对应的 revision。",
    )
    rejected_version = models.ForeignKey(
        "wagtailcore.Revision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        help_text="文章审核驳回时对应的 revision。",
    )

    search_fields = Page.search_fields + [
        index.SearchField("abstract"),
        index.SearchField("body"),
        index.SearchField("authors"),
        index.SearchField("keywords"),
        index.FilterField("article_type"),
        index.FilterField("review_status"),
        index.FilterField("primary_journal"),
    ]

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("abstract"),
                FieldPanel("featured_image"),
                FieldPanel("featured_image_alt"),
            ],
            heading="封面与摘要",
        ),
        FieldPanel("authors"),
        FieldPanel("body"),
        FieldPanel("keywords"),
        PreviewButton(heading="动态预览"),
    ]

    classification_panels = [
        MultiFieldPanel(
            [
                FieldPanel("primary_journal"),
                FieldPanel("related_journals"),
                FieldPanel("article_type"),
            ],
            heading="归属与文章类型",
        ),
        HelpPanel(
            content=(
                '<div class="article-admin-notice">'
                "<strong>审核前必填：</strong>请至少选择一个动态栏目，并且仅勾选一个“主栏目”。"
                "当前主属期刊没有可选栏目时，请先在“栏目管理”中配置栏目；导入流程不会自动创建栏目。"
                "保存草稿后，请在“所有文章”中提交审核；导入不会自动审核、投放或静态发布。"
                "</div>"
            )
        ),
        ArticleCategoryAssignmentInlinePanel(
            "category_assignments",
            label="动态栏目",
            heading="动态栏目（只有一个栏目时自动设为主栏目）",
        ),
    ]

    responsibility_panels = [
        HelpPanel(
            content=(
                '<div class="article-admin-notice">'
                "<strong>流程提示：</strong>审核通过不等于前台发布，仍需进入投放管理，"
                "并由发布管理员执行静态发布。"
                "</div>"
            )
        ),
        MultiFieldPanel(
            [
                FieldPanel("ai_co_authors"),
                FieldPanel("ai_contribution_statement"),
                FieldPanel("responsibility_statement"),
            ],
            heading="AI 参与与责任声明",
        ),
        MultiFieldPanel(
            [
                FieldPanel("review_status", read_only=True),
                FieldPanel("approved_version", read_only=True),
                FieldPanel("rejected_version", read_only=True),
            ],
            heading="审核状态",
        ),
    ]

    promote_panels = Page.promote_panels + [
        MultiFieldPanel(
            [FieldPanel("static_slug")],
            heading="静态路径",
        ),
    ]

    delivery_panels = [
        HelpPanel(
            content=(
                '<div class="article-delivery-summary">'
                "<strong>交付状态（只读）</strong><br>"
                "下列字段由投放和静态发布流程维护，编辑表单不能修改。"
                "</div>"
            )
        ),
        MultiFieldPanel(
            [
                FieldPanel("publication_status", read_only=True),
                FieldPanel("placement_sync_status", read_only=True),
                FieldPanel("build_version", read_only=True),
                FieldPanel("published_version", read_only=True),
                FieldPanel("publish_failure_reason", read_only=True),
                FieldPanel("last_built_at", read_only=True),
                FieldPanel("last_static_published_at", read_only=True),
            ],
            heading="交付摘要",
        ),
        MultiFieldPanel(
            [
                FieldPanel("placement_sync_error", read_only=True),
                FieldPanel("placement_synced_revision_id", read_only=True),
                FieldPanel("placement_sync_request_id", read_only=True),
            ],
            heading="诊断信息",
            classname="collapsed",
        ),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading="正文内容"),
            ObjectList(classification_panels, heading="归属与分类"),
            ObjectList(responsibility_panels, heading="审核与责任"),
            ObjectList(promote_panels, heading="发布与 SEO"),
            ObjectList(delivery_panels, heading="交付状态"),
        ]
    )

    def get_static_output_path(self):
        """Return the one canonical fixed-HTML file path for this article."""
        return f"/articles/{self.static_slug}/index.html"

    def get_absolute_url(self):
        """Return the one canonical production URL, independent of the page tree."""
        return reverse("article_detail", kwargs={"slug": self.static_slug})

    def serve(self, request, *args, **kwargs):
        canonical_url = self.get_absolute_url()
        if request.path_info != canonical_url:
            return HttpResponsePermanentRedirect(canonical_url)
        return super().serve(request, *args, **kwargs)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["article"] = self
        context["static_url"] = self.get_absolute_url()
        context["article_type_label"] = article_type_label(self.article_type)
        context["review_status_label"] = self.get_review_status_display()
        context["related_journals"] = self._get_related_journals_for_preview()
        context.update(get_article_fallback_context(self, request=request))
        return context

    def serve_preview(self, request, mode_name):
        return TemplateResponse(
            request,
            ARTICLE_PREVIEW_TEMPLATE,
            self.get_preview_context(request, mode_name),
        )

    def get_preview_context(self, request, mode_name):
        context = super().get_preview_context(request, mode_name)
        context.update(
            {
                "article": self,
                "page": self,
                "placements": [],
                "related_articles": [],
                "recommended_articles": [],
                "preview_mode": mode_name,
                "is_preview": True,
            }
        )
        context.update(get_article_fallback_context(self, request=request))
        return context

    def save(
        self,
        clean=True,
        user=None,
        log_action=False,
        bypass_article_permission_check=False,
        **kwargs,
    ):
        if not bypass_article_permission_check:
            self._raise_if_user_cannot_save(user)

        if not self.static_slug:
            self.static_slug = self._generate_unique_static_slug()
        super().save(clean=clean, user=user, log_action=log_action, **kwargs)

    def save_revision(
        self,
        user=None,
        *args,
        bypass_article_permission_check=False,
        **kwargs,
    ):
        if not bypass_article_permission_check:
            self._raise_if_user_cannot_save(user)
        return super().save_revision(*args, user=user, **kwargs)

    def submit_for_review(self, user, comment="", expected_revision_id=None):
        from .category_services import validate_article_category_revision

        validate_article_category_revision(article=self, action="submit")
        return self._record_review_action(
            user=user,
            action=self.ReviewStatus.SUBMITTED,
            comment=comment,
            expected_revision_id=expected_revision_id,
        )

    def approve(self, user, comment="", expected_revision_id=None):
        return self._record_review_action(
            user=user,
            action=self.ReviewStatus.APPROVED,
            comment=comment,
            expected_revision_id=expected_revision_id,
        )

    def reject(self, user, comment, expected_revision_id=None):
        if not str(comment or "").strip():
            raise ValidationError("驳回意见必填。")
        return self._record_review_action(
            user=user,
            action=self.ReviewStatus.REJECTED,
            comment=comment,
            expected_revision_id=expected_revision_id,
        )

    def _generate_unique_static_slug(self):
        max_length = self._meta.get_field("static_slug").max_length
        base_slug = slugify(self.title) or "article"
        base_slug = base_slug[:max_length]
        static_slug = base_slug
        suffix = 2
        existing_pages = type(self).objects.all()

        if self.pk:
            existing_pages = existing_pages.exclude(pk=self.pk)

        while existing_pages.filter(static_slug=static_slug).exists():
            suffix_text = f"-{suffix}"
            static_slug = f"{base_slug[: max_length - len(suffix_text)]}{suffix_text}"
            suffix += 1

        return static_slug

    def _get_related_journals_for_preview(self):
        try:
            return self.related_journals.all()
        except ValueError:
            return []

    def _record_review_action(
        self,
        user,
        action,
        comment="",
        expected_revision_id=None,
    ):
        if action in (
            self.ReviewStatus.APPROVED,
            self.ReviewStatus.REJECTED,
        ) and not user_has_article_review_permission(user):
            raise PermissionDenied("User does not have article review permission.")

        with transaction.atomic():
            article = type(self).objects.select_for_update().get(pk=self.pk)
            revision = article._get_or_create_current_review_revision(user)
            if expected_revision_id is not None and str(revision.pk) != str(
                expected_revision_id
            ):
                raise ArticleRevisionConflict(
                    "文章在审核页面打开后已产生新 revision，请刷新后重新审核。"
                )

            article.review_status = action
            if action == self.ReviewStatus.APPROVED:
                if article.publication_status not in (
                    self.PublicationStatus.BUILT,
                    self.PublicationStatus.PUBLISHED,
                ):
                    article.publication_status = self.PublicationStatus.APPROVED
                article.approved_version = revision
            elif action == self.ReviewStatus.REJECTED:
                if article.publication_status:
                    article.publication_status = self.PublicationStatus.OFFLINE
                article.rejected_version = revision

            article.save(bypass_article_permission_check=True)
            from .publication import sync_article_placement_status

            sync_article_placement_status(article.pk)
            article.refresh_from_db(fields=("publication_status",))
            record = ArticleReviewRecord.objects.create(
                article=article,
                reviewer=user,
                revision=revision,
                action=action,
                comment=comment,
            )
            log_article_audit(
                action=action,
                article=article,
                user=user,
                comment=comment,
                metadata={
                    "review_record_id": record.pk,
                    "review_revision_id": revision.pk,
                },
            )
            if action == self.ReviewStatus.APPROVED:
                article_id = article.pk
                revision_id = revision.pk

                def synchronize_approved_categories():
                    from ai_author_forum.placements.category_services import (
                        sync_category_placements,
                    )

                    sync_category_placements(
                        article_id=article_id,
                        revision_id=revision_id,
                        actor=user,
                    )

                transaction.on_commit(synchronize_approved_categories)
            self.review_status = article.review_status
            self.publication_status = article.publication_status
            self.approved_version_id = article.approved_version_id
            self.rejected_version_id = article.rejected_version_id
            return record

    def _get_or_create_current_review_revision(self, user):
        revision = self.get_latest_revision()
        if revision:
            return revision

        return self.save_revision(
            user=user,
            changed=False,
            bypass_article_permission_check=True,
        )

    def _raise_if_user_cannot_save(self, user):
        if user is None or user.is_superuser:
            return

        if not user_has_article_edit_permission(user):
            raise PermissionDenied("User does not have article edit permission.")

        if self.pk and not self.permissions_for_user(user).can_edit():
            raise PermissionDenied("User cannot edit this article page.")

        if (
            self._is_reviewed_for_edit_guard()
            and not self._user_has_strict_change_permission(user)
        ):
            raise PermissionDenied(
                "User needs Wagtail change permission to modify reviewed articles."
            )

    def _is_reviewed_for_edit_guard(self):
        reviewed_statuses = (
            self.ReviewStatus.APPROVED,
            self.ReviewStatus.PUBLISHED,
        )

        if self.review_status in reviewed_statuses:
            return True

        if not self.pk:
            return False

        existing_status = (
            type(self)
            .objects.filter(pk=self.pk)
            .values_list("review_status", flat=True)
            .first()
        )
        return existing_status in reviewed_statuses

    def _user_has_strict_change_permission(self, user):
        if not user.is_active:
            return False

        if user.is_superuser:
            return True

        from wagtail.permissions import page_permission_policy

        for permission in page_permission_policy.get_cached_permissions_for_user(user):
            if (
                permission.permission.codename == "change_page"
                and self.path.startswith(permission.page.path)
            ):
                return True

        return False

    class Meta:
        permissions = [
            ("edit_article", "可编辑文章"),
            ("review_article", "可审核文章"),
            ("trigger_article_placement", "可管理文章投放"),
            ("use_raw_html", "可使用文章 Raw HTML 正文块"),
        ]


class ArticleCategoryAssignment(Orderable):
    article = ParentalKey(
        ArticlePage,
        on_delete=models.CASCADE,
        related_name="category_assignments",
    )
    category = models.ForeignKey(
        "journals.JournalCategory",
        on_delete=models.PROTECT,
        related_name="article_assignments",
    )
    is_primary = models.BooleanField(default=False)

    panels = [
        FieldPanel("category", heading="栏目"),
        FieldPanel("is_primary", heading="设为主栏目（只有一个栏目时自动勾选）"),
    ]

    class Meta(Orderable.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["article", "category"],
                name="uniq_article_category_assignment",
            ),
            models.UniqueConstraint(
                fields=["article"],
                condition=models.Q(is_primary=True),
                name="uniq_primary_category_per_article",
            ),
        ]
        permissions = [
            ("assign_articlecategory", "可分配文章动态栏目"),
        ]

    def clean(self):
        super().clean()
        if not self.article_id or not self.category_id:
            return
        from ai_author_forum.journals.models import JournalCategoryStatus

        errors = {}
        if self.article.primary_journal_id != self.category.journal_id:
            errors["category"] = "Category must belong to the article primary journal."
        if self.category.status not in {
            JournalCategoryStatus.ACTIVE,
            JournalCategoryStatus.HIDDEN,
        }:
            errors["category"] = "Only active or hidden categories may be assigned."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        role = "primary" if self.is_primary else "related"
        return f"{self.article} -> {self.category} ({role})"


class ArticleReviewTask(AbstractGroupApprovalTask):
    def user_can_access_editor(self, obj, user):
        return False

    def locked_for_user(self, obj, user):
        return not self._user_can_review(user)

    def user_can_lock(self, obj, user):
        return self._user_can_review(user)

    def get_actions(self, obj, user):
        if not self._user_can_review(user):
            return []

        return [
            ("reject", "Request changes", True),
            ("approve", "Approve", False),
            ("approve", "Approve with comment", True),
        ]

    def get_task_states_user_can_moderate(self, user, **kwargs):
        if self._user_can_review(user):
            return self.task_states.filter(status=TaskState.STATUS_IN_PROGRESS)

        return TaskState.objects.none()

    @classmethod
    def get_description(cls):
        return "用户必须属于审核任务组，并拥有文章审核权限。"

    def _user_can_review(self, user):
        if not user.is_active:
            return False

        if user.is_superuser:
            return True

        # Business roles are the source of truth for the AI Author Forum admin.
        # Wagtail's GroupApprovalTask still stores a reviewer group, but project
        # leads / super-admin role presets may receive articles.review_article
        # directly without being placed in the narrow task group. In that case
        # the review dashboard was visible while approve/reject actions were
        # hidden. Treat the business review permission as sufficient and keep
        # group membership as a compatibility fallback for legacy reviewers.
        return user_has_article_review_permission(user) or self._user_in_groups(user)

    class Meta:
        verbose_name = "文章审核任务"
        verbose_name_plural = "文章审核任务"


class ArticleReviewRecord(models.Model):
    class Action(models.TextChoices):
        SUBMITTED = "submitted", "已提交审核"
        APPROVED = "approved", "审核通过"
        REJECTED = "rejected", "已驳回"

    article = models.ForeignKey(
        ArticlePage,
        on_delete=models.CASCADE,
        related_name="review_records",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="article_review_records",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    revision = models.ForeignKey(
        "wagtailcore.Revision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="article_review_records",
        help_text="本次审核操作明确针对的文章 revision。",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.article} {self.action} by {self.reviewer}"
