from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.http import HttpResponsePermanentRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import get_language
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import (
    FieldPanel,
    HelpPanel,
    InlinePanel,
    MultiFieldPanel,
    ObjectList,
    TabbedInterface,
)
from wagtail.fields import StreamField
from wagtail.models import (
    AbstractGroupApprovalTask,
    Orderable,
    Page,
    PagePermissionTester,
    TaskState,
)
from wagtail.search import index

from ai_author_forum.utils.i18n import article_type_label
from ai_author_forum.utils.public_i18n import (
    localized_article_abstract,
    localized_article_ai_coauthors,
    localized_article_authors,
    localized_article_body,
    localized_article_keywords,
    localized_article_title,
)

from .blocks import ArticleBodyBlock
from .forms import ArticleCategoryAssignmentInlinePanel, ArticlePageForm
from .integrations import get_article_fallback_context
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

    from ai_author_forum.site_settings.access_control import is_super_admin

    if is_super_admin(user):
        return True

    from ai_author_forum.journals.models import JournalEditorAssignment

    assignments = JournalEditorAssignment.objects.effective().filter(user=user)
    if permission_name == ARTICLE_EDIT_PERMISSION:
        if assignments.filter(
            models.Q(
                role__in=(
                    JournalEditorAssignment.Role.CHIEF_EDITOR,
                    JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
                )
            )
        ).exists():
            return True
        return any(
            JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
            in (responsibilities or [])
            for responsibilities in assignments.values_list(
                "responsibilities", flat=True
            )
        )
    if permission_name == ARTICLE_PLACEMENT_PERMISSION:
        return assignments.filter(
            role__in=(
                JournalEditorAssignment.Role.CHIEF_EDITOR,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
            )
        ).exists()
    return False


def user_has_article_edit_permission(user):
    return user_has_article_permission(user, ARTICLE_EDIT_PERMISSION)


def user_has_article_review_permission(user):
    if user is None or not user.is_active:
        return False
    from ai_author_forum.journals.models import JournalEditorAssignment
    from ai_author_forum.site_settings.access_control import is_super_admin

    return (
        is_super_admin(user)
        or JournalEditorAssignment.objects.effective().filter(user=user).exists()
    )


def user_has_article_placement_permission(user):
    return user_has_article_permission(user, ARTICLE_PLACEMENT_PERMISSION)


def user_has_raw_html_permission(user):
    return user_has_article_permission(user, ARTICLE_RAW_HTML_PERMISSION)


class ArticleRevisionConflict(ValidationError):
    pass


class ArticlePagePermissionTester(PagePermissionTester):
    """Expose Wagtail's editor only through the journal-scoped RBAC service."""

    def _can_access(self):
        from ai_author_forum.site_settings.access_control import (
            get_journal_editor_assignment,
            is_super_admin,
        )

        return is_super_admin(self.user) or bool(
            get_journal_editor_assignment(self.user, self.page.primary_journal)
        )

    def can_edit(self):
        from ai_author_forum.site_settings.access_control import can_manage_article

        return can_manage_article(self.user, self.page)

    def can_view_revisions(self):
        return self._can_access()

    def can_lock(self):
        return self.can_edit()

    def can_unlock(self):
        return self.can_edit()

    def can_submit_for_moderation(self):
        # Submission is handled by submit_article_for_initial_review(), which
        # enforces expected state/revision, idempotency and audit logging.
        return False

    def can_publish(self):
        return False

    def can_unpublish(self):
        return False

    def can_delete(self, ignore_bulk=False):
        return False

    def can_move(self):
        return False

    def can_copy(self):
        return False


class ArticlePage(Page):
    REVIEW_GUARDED_FIELDS = (
        "title",
        "abstract",
        "featured_image",
        "featured_image_alt",
        "authors",
        "ai_co_authors",
        "ai_contribution_statement",
        "responsibility_statement",
        "article_type",
        "primary_journal",
        "keywords",
        "static_slug",
        "body",
    )
    template = ARTICLE_PAGE_TEMPLATE
    page_ptr = models.OneToOneField(
        Page,
        on_delete=models.CASCADE,
        parent_link=True,
        related_name="articles_articlepage",
    )
    base_form_class = ArticlePageForm

    def permissions_for_user(self, user):
        return ArticlePagePermissionTester(user, self)

    class ArticleType(models.TextChoices):
        AI_ARTICLE = "AI Article", "AI 文章"
        NEWS = "News", "新闻"
        OPINION = "Opinion", "观点"
        RESEARCH_ANALYSIS = "Research Analysis", "研究分析"

    class ReviewStatus(models.TextChoices):
        DRAFT = "draft", "草稿"
        SUBMITTED = "submitted", "待初审"
        PENDING_FINAL = "pending_final", "待终审"
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
        blank=True,
        help_text="多个作者请使用英文逗号分隔。",
    )
    ai_co_authors = models.CharField(max_length=255, blank=True)
    ai_contribution_statement = models.TextField(blank=True, verbose_name="AI 参与说明")
    responsibility_statement = models.TextField(blank=True, verbose_name="作者声明")
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
    has_ever_been_submitted = models.BooleanField(
        default=False,
        db_index=True,
        editable=False,
    )
    first_submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
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
    assigned_initial_editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_initial_review_articles",
        editable=False,
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_initial_review_actions",
        editable=False,
    )
    assigned_at = models.DateTimeField(null=True, blank=True, editable=False)
    assignment_request_id = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        editable=False,
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
        InlinePanel(
            "contributors",
            label="作者或编辑",
            heading="作者",
            min_num=1,
            help_text=(
                "添加文章作者和编辑信息。可选择预设身份，或选择“自定义身份”后填写新身份名称。"
            ),
        ),
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
            ],
            heading="AI 参与说明",
        ),
        MultiFieldPanel(
            [FieldPanel("responsibility_statement")],
            heading="作者声明",
            help_text="纯文本字段；可留空，HTML、脚本和 iframe 不会作为标记渲染。",
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

    def sync_authors_from_contributors(self):
        """Keep the legacy search/import field aligned with structured people."""
        if not self.pk:
            return self.authors

        contributors = list(self.contributors.all())
        if not contributors:
            summary = ""
        else:
            author_names = [
                contributor.name
                for contributor in contributors
                if contributor.identity == ArticleContributor.Identity.AUTHOR
            ]
            summary = ", ".join(author_names or [item.name for item in contributors])

        if self.authors != summary:
            type(self).objects.filter(pk=self.pk).update(authors=summary)
            self.authors = summary
        return summary

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
        context["article_display_title"] = localized_article_title(self)
        context["article_display_abstract"] = localized_article_abstract(self)
        context["article_display_authors"] = localized_article_authors(self)
        context["authors_text"] = context["article_display_authors"]
        context["contributors"] = tuple(self.contributors.all())
        context["article_display_keywords"] = localized_article_keywords(self)
        context["article_display_body"] = localized_article_body(self)
        context["review_status_label"] = self.get_review_status_display()
        context["related_journals"] = self._get_related_journals_for_preview()
        context.update(self._declaration_context())
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
        context.update(self._declaration_context())
        context.update(get_article_fallback_context(self, request=request))
        return context

    def _declaration_context(self):
        from ai_author_forum.journals.frontend import get_public_editorial_team

        ai_coauthors = localized_article_ai_coauthors(self)
        editorial_team = get_public_editorial_team(self.primary_journal)
        if not self.primary_journal.show_editorial_team_on_article_pages:
            editorial_team = {
                "heading": editorial_team["heading"],
                "groups": [],
                "has_members": False,
            }
        return {
            "primary_journal": self.primary_journal,
            "author_declaration": self.responsibility_statement,
            "editorial_team": editorial_team,
            "ai": {
                "co_authors_text": ai_coauthors,
                "contribution_statement": self.ai_contribution_statement,
                "responsibility_statement": self.responsibility_statement,
                "has_contribution": bool(
                    ai_coauthors or self.ai_contribution_statement
                ),
            },
        }

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
            self._raise_if_review_projection_written_directly()
        update_fields = kwargs.get("update_fields")
        review_fields_are_being_saved = update_fields is None or bool(
            set(update_fields) & set(self.REVIEW_GUARDED_FIELDS)
        )
        reset_review = (
            review_fields_are_being_saved and self._approved_content_changed()
        )
        previous_approved_revision_id = self.approved_version_id
        if reset_review:
            self.review_status = self.ReviewStatus.DRAFT
            self.approved_version = None
            self.assigned_initial_editor = None
            self.assigned_by = None
            self.assigned_at = None
            self.assignment_request_id = None
            if self.publication_status:
                self.publication_status = self.PublicationStatus.OFFLINE

        with transaction.atomic():
            if not self.static_slug:
                self.static_slug = self._generate_unique_static_slug()
            super().save(clean=clean, user=user, log_action=log_action, **kwargs)
            if reset_review:
                from ai_author_forum.site_settings.models import (
                    AuditAction,
                    AuditLog,
                    AuditStatus,
                )

                from .publication import sync_article_placement_status

                AuditLog.record(
                    action=AuditAction.PERMISSION,
                    status=AuditStatus.SUCCESS,
                    actor=user,
                    target=self,
                    message="终审后的正文或作者声明发生修改，撤销旧审核投影并返回草稿。",
                    metadata={
                        "previous_approved_revision_id": previous_approved_revision_id
                    },
                )
                sync_article_placement_status(self.pk)

    def _raise_if_review_projection_written_directly(self):
        if not self.pk:
            if (
                self.review_status
                in {
                    self.ReviewStatus.PENDING_FINAL,
                    self.ReviewStatus.APPROVED,
                    self.ReviewStatus.PUBLISHED,
                }
                or self.approved_version_id
            ):
                raise ValidationError("审核投影只能由两级审核 service 创建。")
            return
        previous = (
            type(self)
            .objects.filter(pk=self.pk)
            .only(
                "review_status",
                "approved_version_id",
            )
            .first()
        )
        if previous is None:
            return
        if (
            self.review_status != previous.review_status
            and self.review_status
            in {
                self.ReviewStatus.PENDING_FINAL,
                self.ReviewStatus.APPROVED,
                self.ReviewStatus.PUBLISHED,
            }
        ) or self.approved_version_id != previous.approved_version_id:
            raise ValidationError("审核状态和批准 revision 只能由审核 service 写入。")

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

    def _approved_content_changed(self):
        if not self.pk:
            return False
        previous = type(self).objects.filter(pk=self.pk).first()
        if previous is None or previous.review_status not in {
            self.ReviewStatus.APPROVED,
            self.ReviewStatus.PUBLISHED,
        }:
            return False
        fields = tuple(
            (
                f"{field_name}_id"
                if field_name in {"featured_image", "primary_journal"}
                else field_name
            )
            for field_name in self.REVIEW_GUARDED_FIELDS
            if field_name != "body"
        )
        if any(getattr(previous, field) != getattr(self, field) for field in fields):
            return True
        return previous.body.raw_data != self.body.raw_data

    def submit_for_review(
        self,
        user,
        comment="",
        expected_revision_id=None,
        request_id=None,
    ):
        from .review_services import submit_article_for_initial_review

        revision = self.get_latest_revision()
        return submit_article_for_initial_review(
            actor=user,
            article=self,
            expected_state=self.ReviewStatus.DRAFT,
            expected_revision_id=expected_revision_id or getattr(revision, "pk", None),
            request_id=request_id or uuid4(),
            comment=comment,
        )

    def approve(
        self,
        user,
        comment="",
        expected_revision_id=None,
        request_id=None,
    ):
        from .review_services import final_review_article, initial_review_article

        revision = self.get_latest_revision()
        values = {
            "actor": user,
            "article": self,
            "action": "approve",
            "comment": comment,
            "expected_state": self.review_status,
            "expected_revision_id": expected_revision_id
            or getattr(revision, "pk", None),
            "request_id": request_id or uuid4(),
        }
        if self.review_status == self.ReviewStatus.SUBMITTED:
            return initial_review_article(**values)
        return final_review_article(**values)

    def reject(
        self,
        user,
        comment,
        expected_revision_id=None,
        request_id=None,
    ):
        from .review_services import final_review_article, initial_review_article

        revision = self.get_latest_revision()
        values = {
            "actor": user,
            "article": self,
            "action": "reject",
            "comment": comment,
            "expected_state": self.review_status,
            "expected_revision_id": expected_revision_id
            or getattr(revision, "pk", None),
            "request_id": request_id or uuid4(),
        }
        if self.review_status == self.ReviewStatus.SUBMITTED:
            return initial_review_article(**values)
        return final_review_article(**values)

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
        if user is None:
            return
        from ai_author_forum.site_settings.access_control import can_manage_article

        if not can_manage_article(user, self):
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

        from ai_author_forum.site_settings.access_control import can_manage_article

        if can_manage_article(user, self):
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


class ArticleAuthorshipQuerySet(models.QuerySet):
    def effective(self):
        return self.filter(
            revoked_at__isnull=True,
            user__is_active=True,
            user__account_status="active",
            user__is_author=True,
        )


class ArticleAuthorship(models.Model):
    """Object-level author access; public contributor names are not authority."""

    class Role(models.TextChoices):
        OWNER = "owner", "投稿负责人"
        CO_AUTHOR = "co_author", "共同作者"

    article = models.ForeignKey(
        ArticlePage,
        on_delete=models.PROTECT,
        related_name="authorships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="article_authorships",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    can_edit = models.BooleanField(default=False)
    is_corresponding = models.BooleanField(default=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invited_article_authorships",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ArticleAuthorshipQuerySet.as_manager()

    class Meta:
        ordering = ["article_id", "role", "created_at", "pk"]
        indexes = [
            models.Index(fields=["user", "revoked_at", "can_edit"]),
            models.Index(fields=["article", "revoked_at", "role"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["article", "user"],
                name="articles_unique_article_user_authorship",
            ),
            models.UniqueConstraint(
                fields=["article"],
                condition=models.Q(role="owner", revoked_at__isnull=True),
                name="articles_one_effective_submission_owner",
            ),
            models.UniqueConstraint(
                fields=["article"],
                condition=models.Q(is_corresponding=True, revoked_at__isnull=True),
                name="articles_one_effective_corresponding_author",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(can_edit=False)
                    | models.Q(role="owner")
                    | models.Q(accepted_at__isnull=False)
                ),
                name="articles_authorship_edit_requires_acceptance",
            ),
        ]
        permissions = [
            ("manage_article_authorship", "可管理文章投稿关系"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.revoked_at is not None:
            self.can_edit = False
        elif self.role == self.Role.OWNER:
            self.can_edit = True
            self.accepted_at = self.accepted_at or timezone.now()
        elif self.can_edit and self.accepted_at is None:
            errors["can_edit"] = "共同作者接受邀请后才能获得编辑权。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("投稿关系必须撤销，不能删除。")

    @property
    def is_effective(self):
        return bool(
            self.revoked_at is None
            and self.user.is_active
            and self.user.account_status == "active"
            and self.user.is_author
        )

    def __str__(self):
        return f"{self.article}: {self.user} ({self.get_role_display()})"


class AuthorSubmissionOperationQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("作者投稿操作记录创建后不可修改。")

    def delete(self):
        raise ValidationError("作者投稿操作记录创建后不可删除。")


class AuthorSubmissionOperation(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "创建投稿"
        SAVE = "save", "保存投稿"
        CHANGE_JOURNAL = "change_journal", "作者更换期刊"
        SUBMIT = "submit", "提交初审"
        GRANT = "grant", "授予投稿关系"
        REVOKE = "revoke", "撤销投稿关系"
        TRANSFER = "transfer", "编辑受控转投"

    request_id = models.UUIDField(unique=True)
    action = models.CharField(max_length=24, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="author_submission_operations",
    )
    article = models.ForeignKey(
        ArticlePage,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="author_operations",
    )
    authorship = models.ForeignKey(
        ArticleAuthorship,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="operations",
    )
    revision = models.ForeignKey(
        "wagtailcore.Revision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="author_submission_operations",
    )
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuthorSubmissionOperationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["article", "action", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("作者投稿操作记录创建后不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("作者投稿操作记录创建后不可删除。")


class AuthorSubmissionAsset(models.Model):
    class Kind(models.TextChoices):
        COVER = "cover", "文章封面"
        INLINE_IMAGE = "inline_image", "正文图片"
        ATTACHMENT = "attachment", "投稿附件"

    class ScanStatus(models.TextChoices):
        PENDING = "pending", "待扫描"
        CLEAN = "clean", "通过"
        REJECTED = "rejected", "已拒绝"

    article = models.ForeignKey(
        ArticlePage,
        on_delete=models.PROTECT,
        related_name="author_assets",
    )
    authorship = models.ForeignKey(
        ArticleAuthorship,
        on_delete=models.PROTECT,
        related_name="assets",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="author_submission_assets",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    image = models.ForeignKey(
        "images.CustomImage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="author_submission_assets",
    )
    document = models.ForeignKey(
        "wagtaildocs.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="author_submission_assets",
    )
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    scan_status = models.CharField(
        max_length=12,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING,
        db_index=True,
    )
    scan_detail = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["article_id", "kind", "-created_at", "-pk"]
        indexes = [models.Index(fields=["article", "kind", "is_active"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind__in=["cover", "inline_image"],
                        image__isnull=False,
                        document__isnull=True,
                    )
                    | models.Q(
                        kind="attachment",
                        image__isnull=True,
                        document__isnull=False,
                    )
                ),
                name="articles_author_asset_kind_payload",
            )
        ]

    def clean(self):
        super().clean()
        if self.kind in {self.Kind.COVER, self.Kind.INLINE_IMAGE}:
            if self.image_id is None or self.document_id is not None:
                raise ValidationError("图片资产必须且只能关联一张图片。")
        elif self.kind == self.Kind.ATTACHMENT:
            if self.document_id is None or self.image_id is not None:
                raise ValidationError("附件资产必须且只能关联一个文档。")

    def delete(self, *args, **kwargs):
        raise ValidationError("作者投稿资产必须停用，不能删除。")


class ArticleContributor(Orderable):
    """A person credited on an article, with an editorial identity when needed."""

    class Identity(models.TextChoices):
        AUTHOR = "author", "作者"
        EDITOR_IN_CHIEF = "editor_in_chief", "主编"
        EXECUTIVE_EDITOR = "executive_editor", "执行主编"
        ASSOCIATE_EDITOR = "associate_editor", "副编辑"
        CUSTOM = "custom", "自定义身份"

    ENGLISH_IDENTITIES = {
        Identity.AUTHOR: "Author",
        Identity.EDITOR_IN_CHIEF: "Editor-in-Chief",
        Identity.EXECUTIVE_EDITOR: "Executive Editor",
        Identity.ASSOCIATE_EDITOR: "Associate Editor",
    }

    article = ParentalKey(
        ArticlePage,
        on_delete=models.CASCADE,
        related_name="contributors",
    )
    identity = models.CharField(
        max_length=32,
        choices=Identity.choices,
        default=Identity.AUTHOR,
        verbose_name="身份",
    )
    custom_identity = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="自定义身份",
        help_text="仅在身份选择“自定义身份”时填写。",
    )
    name = models.CharField(max_length=255, verbose_name="姓名")
    affiliation = models.CharField(max_length=500, blank=True, verbose_name="单位")
    is_corresponding = models.BooleanField(default=False, verbose_name="通讯作者")

    panels = [
        FieldPanel("identity"),
        FieldPanel("custom_identity"),
        FieldPanel("name"),
        FieldPanel("affiliation"),
        FieldPanel("is_corresponding"),
    ]

    class Meta(Orderable.Meta):
        verbose_name = "文章作者或编辑"
        verbose_name_plural = "文章作者或编辑"

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        self.custom_identity = (self.custom_identity or "").strip()
        if self.identity == self.Identity.CUSTOM and not self.custom_identity:
            raise ValidationError({"custom_identity": "请选择或填写自定义身份名称。"})
        if self.identity != self.Identity.CUSTOM:
            self.custom_identity = ""

    def display_identity(self, language_code=None):
        if self.identity == self.Identity.CUSTOM:
            return self.custom_identity
        active_language = language_code or get_language()
        if str(active_language or "").lower().startswith("en"):
            return self.ENGLISH_IDENTITIES.get(
                self.identity, self.get_identity_display()
            )
        return self.get_identity_display()

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        if self.article_id:
            ArticlePage.objects.get(pk=self.article_id).sync_authors_from_contributors()
        return result

    def delete(self, *args, **kwargs):
        article_id = self.article_id
        result = super().delete(*args, **kwargs)
        if article_id:
            ArticlePage.objects.get(pk=article_id).sync_authors_from_contributors()
        return result

    def __str__(self):
        return f"{self.display_identity()}: {self.name}"


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
    """Compatibility task retained for one migration cycle only."""

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
        return self._user_in_groups(user)

    class Meta:
        verbose_name = "文章审核任务"
        verbose_name_plural = "文章审核任务"


class ArticleInitialReviewTask(AbstractGroupApprovalTask):
    def user_can_access_editor(self, obj, user):
        return False

    def locked_for_user(self, obj, user):
        return not self._user_can_review(obj, user)

    def user_can_lock(self, obj, user):
        return self._user_can_review(obj, user)

    def get_actions(self, obj, user):
        if not self._user_can_review(obj, user):
            return []
        return [
            ("reject", "退回修改", True),
            ("approve", "初审通过", False),
            ("approve", "初审通过并填写意见", True),
        ]

    @transaction.atomic
    def on_action(self, task_state, user, action_name, **kwargs):
        from .review_services import initial_review_article

        article = task_state.workflow_state.get_content_object().specific
        revision = article.get_latest_revision()
        if action_name == "approve":
            task_state.approve(
                user=user, update=False, comment=kwargs.get("comment", "")
            )
        else:
            task_state.reject(
                user=user, update=False, comment=kwargs.get("comment", "")
            )
        initial_review_article(
            actor=user,
            article=article,
            action="approve" if action_name == "approve" else "return",
            comment=str(kwargs.get("comment", "")),
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=getattr(revision, "pk", None),
            request_id=kwargs.get("request_id") or uuid4(),
        )
        workflow_state = task_state.workflow_state
        if action_name == "approve":
            workflow_state.update(user=user)
        else:
            workflow_state.status = workflow_state.STATUS_NEEDS_CHANGES
            workflow_state.save(update_fields=["status"])

    def get_task_states_user_can_moderate(self, user, **kwargs):
        from ai_author_forum.site_settings.access_control import (
            filter_accessible_articles,
        )

        article_ids = [
            str(pk)
            for pk in filter_accessible_articles(
                user, ArticlePage.objects.all()
            ).values_list("pk", flat=True)
        ]
        return self.task_states.filter(
            status=TaskState.STATUS_IN_PROGRESS,
            workflow_state__object_id__in=article_ids,
        )

    def _user_can_review(self, obj, user):
        from ai_author_forum.site_settings.access_control import can_initial_review

        article = getattr(obj, "specific", obj)
        return isinstance(article, ArticlePage) and can_initial_review(user, article)

    @classmethod
    def get_description(cls):
        return "本刊有效编辑可对当前 revision 执行初审。"

    class Meta:
        verbose_name = "文章初审任务"
        verbose_name_plural = "文章初审任务"


class ArticleFinalReviewTask(AbstractGroupApprovalTask):
    def user_can_access_editor(self, obj, user):
        return False

    def locked_for_user(self, obj, user):
        return not self._user_can_review(obj, user)

    def user_can_lock(self, obj, user):
        return self._user_can_review(obj, user)

    def get_actions(self, obj, user):
        if not self._user_can_review(obj, user):
            return []
        return [
            ("reject", "终审退回", True),
            ("approve", "终审通过", False),
            ("approve", "终审通过并填写意见", True),
        ]

    @transaction.atomic
    def on_action(self, task_state, user, action_name, **kwargs):
        from .review_services import final_review_article

        article = task_state.workflow_state.get_content_object().specific
        revision = article.get_latest_revision()
        if action_name == "approve":
            task_state.approve(
                user=user,
                update=False,
                comment=kwargs.get("comment", ""),
            )
        else:
            task_state.reject(
                user=user, update=False, comment=kwargs.get("comment", "")
            )
        final_review_article(
            actor=user,
            article=article,
            action="approve" if action_name == "approve" else "return",
            comment=str(kwargs.get("comment", "")),
            expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
            expected_revision_id=getattr(revision, "pk", None),
            request_id=kwargs.get("request_id") or uuid4(),
        )
        if action_name == "approve":
            workflow_state = task_state.workflow_state
            workflow_state.status = workflow_state.STATUS_APPROVED
            workflow_state.save(update_fields=["status"])
        else:
            workflow_state = task_state.workflow_state
            workflow_state.status = workflow_state.STATUS_NEEDS_CHANGES
            workflow_state.save(update_fields=["status"])

    def get_task_states_user_can_moderate(self, user, **kwargs):
        from ai_author_forum.journals.models import JournalEditorAssignment

        journal_ids = (
            JournalEditorAssignment.objects.effective()
            .filter(
                user=user,
                role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            )
            .values_list("journal_id", flat=True)
        )
        article_ids = [
            str(pk)
            for pk in ArticlePage.objects.filter(
                primary_journal_id__in=journal_ids
            ).values_list("pk", flat=True)
        ]
        return self.task_states.filter(
            status=TaskState.STATUS_IN_PROGRESS,
            workflow_state__object_id__in=article_ids,
        )

    def _user_can_review(self, obj, user):
        from ai_author_forum.site_settings.access_control import can_final_review

        article = getattr(obj, "specific", obj)
        return isinstance(article, ArticlePage) and can_final_review(user, article)

    @classmethod
    def get_description(cls):
        return "只有本刊有效主编辑可对同一 revision 执行终审。"

    class Meta:
        verbose_name = "文章终审任务"
        verbose_name_plural = "文章终审任务"


class ImmutableReviewRecordQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("审核记录创建后不可修改。")

    def delete(self):
        raise ValidationError("审核记录创建后不可删除。")


class ArticleReviewRecord(models.Model):
    class Stage(models.TextChoices):
        INITIAL = "initial", "初审"
        FINAL = "final", "终审"

    class Action(models.TextChoices):
        SUBMIT = "submit", "提交初审"
        INITIAL_APPROVE = "initial_approve", "初审通过"
        INITIAL_RETURN = "initial_return", "初审退回"
        INITIAL_REJECT = "initial_reject", "初审拒绝"
        FINAL_APPROVE = "final_approve", "终审通过"
        FINAL_RETURN = "final_return", "终审退回"
        FINAL_REJECT = "final_reject", "终审拒绝"
        REOPEN = "reopen", "重新开启"
        TRANSFER = "transfer", "受控转投"

    article = models.ForeignKey(
        ArticlePage,
        on_delete=models.PROTECT,
        related_name="review_records",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="article_review_records",
    )
    stage = models.CharField(
        max_length=12,
        choices=Stage.choices,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    revision = models.ForeignKey(
        "wagtailcore.Revision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="article_review_records",
        help_text="本次审核操作明确针对的文章 revision。",
    )
    journal_editor_assignment = models.ForeignKey(
        "journals.JournalEditorAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="article_review_records",
    )
    reviewer_role = models.CharField(max_length=24, blank=True)
    request_id = models.UUIDField(null=True, blank=True, unique=True)
    comment = models.TextField(blank=True)
    author_visible_comment = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True)
    submission_owner = models.ForeignKey(
        ArticleAuthorship,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="submission_review_records",
    )
    submission_journal = models.ForeignKey(
        "journals.Journal",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="author_submission_review_records",
    )
    authorship_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableReviewRecordQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.article} {self.action} by {self.reviewer}"

    def clean(self):
        super().clean()
        initial_actions = {
            self.Action.SUBMIT,
            self.Action.INITIAL_APPROVE,
            self.Action.INITIAL_RETURN,
            self.Action.INITIAL_REJECT,
            self.Action.REOPEN,
            self.Action.TRANSFER,
        }
        final_actions = {
            self.Action.FINAL_APPROVE,
            self.Action.FINAL_RETURN,
            self.Action.FINAL_REJECT,
        }
        errors = {}
        if self.action in initial_actions and self.stage != self.Stage.INITIAL:
            errors["stage"] = "初审动作必须记录为 initial 阶段。"
        if self.action in final_actions and self.stage != self.Stage.FINAL:
            errors["stage"] = "终审动作必须记录为 final 阶段。"
        if self.action in initial_actions | final_actions:
            if not self.revision_id:
                errors["revision"] = "审核记录必须绑定固定 revision。"
            if not self.request_id:
                errors["request_id"] = "审核记录必须包含幂等 request id。"
            if not self.reviewer_role:
                errors["reviewer_role"] = "审核记录必须保存操作时角色快照。"
        if self.action in final_actions and not self.journal_editor_assignment_id:
            errors["journal_editor_assignment"] = "终审记录必须绑定主编辑任命。"
        if (
            self.action
            in {
                self.Action.INITIAL_RETURN,
                self.Action.INITIAL_REJECT,
                self.Action.FINAL_RETURN,
                self.Action.FINAL_REJECT,
                self.Action.REOPEN,
            }
            and not self.comment.strip()
        ):
            errors["comment"] = "退回、拒绝或重新开启必须填写意见。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("审核记录创建后不可修改。")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("审核记录创建后不可删除。")
