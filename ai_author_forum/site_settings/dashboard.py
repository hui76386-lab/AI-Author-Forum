from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import Count, F, Max, OuterRef, Q, Subquery
from django.urls import reverse
from django.utils import timezone
from wagtail.admin.ui.components import Component

from ai_author_forum.articles.models import ArticlePage, ArticleReviewRecord
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalCategoryStatus,
    JournalStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.static_publish.models import (
    StaticManifest,
    StaticPublishJob,
    StaticPublishTarget,
)

from .models import AuditLog, AuditStatus
from .permissions import get_admin_permission_context

RECENT_DAYS = 7
REVIEW_OVERDUE_HOURS = 48


def _url(name: str, **query) -> str:
    url = reverse(name)
    filtered = {key: value for key, value in query.items() if value not in (None, "")}
    if filtered:
        return f"{url}?{urlencode(filtered)}"
    return url


def _metric(label, value, url, *, description="", tone="neutral"):
    return {
        "label": label,
        "value": value,
        "url": url,
        "description": description,
        "tone": tone,
    }


def _section(code, title, description, metrics, *, items=()):
    return {
        "code": code,
        "title": title,
        "description": description,
        "metrics": metrics,
        "items": items,
    }


def _content_section(user):
    recent_from = timezone.localdate() - timedelta(days=RECENT_DAYS)
    stats = ArticlePage.objects.aggregate(
        drafts=Count(
            "pk",
            filter=Q(owner_id=user.pk, review_status=ArticlePage.ReviewStatus.DRAFT),
        ),
        rejected=Count(
            "pk",
            filter=Q(
                owner_id=user.pk,
                review_status=ArticlePage.ReviewStatus.REJECTED,
            ),
        ),
        awaiting_placement=Count(
            "pk",
            filter=Q(
                review_status=ArticlePage.ReviewStatus.APPROVED,
                publication_status=ArticlePage.PublicationStatus.APPROVED,
            ),
        ),
        recent_edits=Count(
            "pk",
            filter=Q(
                owner_id=user.pk,
                latest_revision_created_at__date__gte=recent_from,
            ),
        ),
    )
    recent_items = list(
        ArticlePage.objects.filter(owner_id=user.pk)
        .order_by("-latest_revision_created_at", "-pk")
        .values("pk", "title", "latest_revision_created_at")[:5]
    )
    for item in recent_items:
        item["url"] = reverse("wagtailadmin_pages:edit", args=[item["pk"]])

    return _section(
        "content",
        "内容工作台",
        "聚合展示个人编辑待办和已审核但尚未完成投放的文章。",
        [
            _metric(
                "我的草稿",
                stats["drafts"],
                _url(
                    "article_admin:index",
                    review_status=ArticlePage.ReviewStatus.DRAFT,
                    owner=user.pk,
                ),
            ),
            _metric(
                "被驳回",
                stats["rejected"],
                _url(
                    "article_admin:index",
                    review_status=ArticlePage.ReviewStatus.REJECTED,
                    owner=user.pk,
                ),
                tone="warning",
            ),
            _metric(
                "待投放",
                stats["awaiting_placement"],
                _url(
                    "article_admin:index",
                    review_status=ArticlePage.ReviewStatus.APPROVED,
                    publication_status=ArticlePage.PublicationStatus.APPROVED,
                ),
            ),
            _metric(
                f"近 {RECENT_DAYS} 天编辑",
                stats["recent_edits"],
                _url(
                    "article_admin:index",
                    owner=user.pk,
                    updated_from=recent_from.isoformat(),
                ),
            ),
        ],
        items=recent_items,
    )


def _review_section(user):
    now = timezone.now()
    overdue_before = now - timedelta(hours=REVIEW_OVERDUE_HOURS)
    recent_from = timezone.localdate() - timedelta(days=RECENT_DAYS)
    submissions = ArticleReviewRecord.objects.filter(
        article_id=OuterRef("pk"),
        action=ArticleReviewRecord.Action.SUBMITTED,
    ).order_by("-created_at", "-pk")
    article_stats = ArticlePage.objects.annotate(
        submitted_at=Subquery(submissions.values("created_at")[:1])
    ).aggregate(
        pending=Count(
            "pk",
            filter=Q(review_status=ArticlePage.ReviewStatus.SUBMITTED),
        ),
        overdue=Count(
            "pk",
            filter=Q(
                review_status=ArticlePage.ReviewStatus.SUBMITTED,
                submitted_at__lte=overdue_before,
            ),
        ),
    )
    review_stats = ArticleReviewRecord.objects.filter(
        reviewer_id=user.pk,
        action__in=(
            ArticleReviewRecord.Action.APPROVED,
            ArticleReviewRecord.Action.REJECTED,
        ),
        created_at__date__gte=recent_from,
    ).aggregate(recent=Count("pk"))
    recent_items = list(
        ArticleReviewRecord.objects.filter(
            reviewer_id=user.pk,
            action__in=(
                ArticleReviewRecord.Action.APPROVED,
                ArticleReviewRecord.Action.REJECTED,
            ),
        )
        .order_by("-created_at", "-pk")
        .values("article_id", "article__title", "action", "created_at")[:5]
    )
    for item in recent_items:
        item["title"] = item.pop("article__title")
        item["url"] = reverse("article_admin:review_detail", args=[item["article_id"]])

    return _section(
        "review",
        "审核工作台",
        f"超时待审按提交后超过 {REVIEW_OVERDUE_HOURS} 小时计算。",
        [
            _metric(
                "待审核",
                article_stats["pending"],
                _url(
                    "article_admin:pending",
                    review_status=ArticlePage.ReviewStatus.SUBMITTED,
                ),
            ),
            _metric(
                "超时待审",
                article_stats["overdue"],
                _url(
                    "article_admin:pending",
                    review_status=ArticlePage.ReviewStatus.SUBMITTED,
                    waiting=f"{REVIEW_OVERDUE_HOURS}h",
                ),
                tone="warning",
            ),
            _metric(
                f"近 {RECENT_DAYS} 天审核",
                review_stats["recent"],
                _url(
                    "article_admin:index",
                    reviewed_by=user.pk,
                    reviewed_from=recent_from.isoformat(),
                ),
            ),
        ],
        items=recent_items,
    )


def _operations_section(flags):
    metrics = []
    if flags["can_view_journals"]:
        journal_stats = Journal.objects.aggregate(
            active=Count("pk", filter=Q(status=JournalStatus.ACTIVE)),
            paused=Count("pk", filter=Q(status=JournalStatus.PAUSED)),
        )
        metrics.extend(
            [
                _metric(
                    "\u542f\u7528\u5b50\u671f\u520a",
                    journal_stats["active"],
                    _url("journals:index", status=JournalStatus.ACTIVE),
                ),
                _metric(
                    "\u6682\u505c\u5b50\u671f\u520a",
                    journal_stats["paused"],
                    _url("journals:index", status=JournalStatus.PAUSED),
                    tone="warning",
                ),
            ]
        )
        if flags["can_view_journal_categories"]:
            anomaly_statuses = (
                JournalCategoryStatus.DISABLED,
                JournalCategoryStatus.ARCHIVED,
            )
            category_anomalies = JournalCategory.objects.filter(
                status__in=anomaly_statuses,
            ).count()
            metrics.append(
                _metric(
                    "\u680f\u76ee\u5f02\u5e38",
                    category_anomalies,
                    _url(
                        "journals_category_admin",
                        status="exception",
                    ),
                    tone="warning",
                )
            )

    if flags["can_view_placements"] or flags["can_view_slots"]:
        now = timezone.now()
        expires_before = now + timedelta(days=RECENT_DAYS)
        manual_placements = ArticlePlacement.objects.filter(
            source=ArticlePlacement.Source.MANUAL
        )
        placement_stats = manual_placements.aggregate(
            expiring=Count(
                "pk",
                filter=Q(
                    is_active=True,
                    ends_at__gt=now,
                    ends_at__lte=expires_before,
                ),
            ),
            expired_active=Count(
                "pk",
                filter=Q(is_active=True, ends_at__lte=now),
            ),
        )
        over_capacity = (
            manual_placements.filter(is_active=True, slot__is_active=True)
            .values("slot_id", "target_type", "target_slug", "target_category_id")
            .annotate(item_count=Count("pk"), capacity=F("slot__max_items"))
            .filter(item_count__gt=F("capacity"))
            .count()
        )
        metrics.extend(
            [
                _metric(
                    "超容量版位",
                    over_capacity,
                    _url("placements:index", capacity="over"),
                    tone="warning",
                ),
                _metric(
                    f"{RECENT_DAYS} 天内到期",
                    placement_stats["expiring"],
                    _url("placements:index", expires_within=RECENT_DAYS),
                ),
                _metric(
                    "已到期仍启用",
                    placement_stats["expired_active"],
                    _url("placements:index", expired=1, active=1),
                    tone="danger",
                ),
            ]
        )

    return _section(
        "operations",
        "站点运营工作台",
        "汇总子期刊、栏目和受控版位的运行异常。",
        metrics,
    )


def _publishing_section():
    recent_from = timezone.localdate() - timedelta(days=RECENT_DAYS)
    job_stats = StaticPublishJob.objects.aggregate(
        recent=Count("pk", filter=Q(created_at__date__gte=recent_from)),
        failed=Count("pk", filter=Q(status=StaticPublishJob.Status.FAILED)),
    )
    failed_targets = StaticPublishTarget.objects.filter(
        status=StaticPublishTarget.Status.FAILED
    ).count()
    manifest_stats = StaticManifest.objects.aggregate(
        active_version=Max("version", filter=Q(is_active=True)),
        rollback_versions=Count("pk", filter=Q(is_active=False)),
    )
    return _section(
        "publishing",
        "静态发布工作台",
        "展示发布结果和可恢复版本；实际发布、重试和回滚仍在发布中心执行。",
        [
            _metric(
                f"近 {RECENT_DAYS} 天发布",
                job_stats["recent"],
                _url("static_publish:center", created_from=recent_from.isoformat()),
            ),
            _metric(
                "失败发布任务",
                job_stats["failed"],
                _url("static_publish:center", status=StaticPublishJob.Status.FAILED),
                tone="danger",
            ),
            _metric(
                "失败目标",
                failed_targets,
                _url(
                    "static_publish:center",
                    target_status=StaticPublishTarget.Status.FAILED,
                ),
                tone="danger",
            ),
            _metric(
                "当前活动版本",
                manifest_stats["active_version"] or "暂无",
                _url("static_publish:center", manifest_status="active"),
            ),
            _metric(
                "可回滚版本",
                manifest_stats["rollback_versions"],
                _url("static_publish:center", manifest_status="rollback"),
            ),
        ],
    )


def _readonly_section(flags):
    metrics = []
    if flags["can_view_static_publish"]:
        publish_stats = StaticPublishJob.objects.aggregate(
            pending=Count("pk", filter=Q(status=StaticPublishJob.Status.PENDING)),
            failed=Count("pk", filter=Q(status=StaticPublishJob.Status.FAILED)),
        )
        active_version = StaticManifest.objects.aggregate(
            version=Max("version", filter=Q(is_active=True))
        )["version"]
        metrics.extend(
            [
                _metric(
                    "等待发布",
                    publish_stats["pending"],
                    _url(
                        "static_publish:center", status=StaticPublishJob.Status.PENDING
                    ),
                ),
                _metric(
                    "发布失败",
                    publish_stats["failed"],
                    _url(
                        "static_publish:center",
                        status=StaticPublishJob.Status.FAILED,
                    ),
                    tone="danger",
                ),
                _metric(
                    "活动版本",
                    active_version or "暂无",
                    _url("static_publish:center", manifest_status="active"),
                ),
            ]
        )
    if flags["can_view_slots"]:
        active_slots = LayoutSlot.objects.filter(
            scope=LayoutSlot.Scope.HOME,
            is_active=True,
        ).count()
        metrics.append(
            _metric(
                "启用主站版位",
                active_slots,
                _url("layout-slots:index", scope=LayoutSlot.Scope.HOME, active=1),
            )
        )
    if flags["can_view_audit_log"]:
        failed_audits = AuditLog.objects.filter(status=AuditStatus.FAILURE).count()
        metrics.append(
            _metric(
                "失败审计",
                failed_audits,
                _url("auditlog:index", status=AuditStatus.FAILURE),
                tone="danger",
            )
        )

    return _section(
        "readonly",
        "只读摘要",
        "仅提供查看入口，不提供保存、导入、发布、重试或回滚操作。",
        metrics,
    )


def _find_metric(sections, *labels):
    for section in sections:
        for metric in section["metrics"]:
            if metric["label"] in labels:
                return metric
    return None


def _step(number, title, description, *, url="", metric=None):
    return {
        "number": number,
        "title": title,
        "description": description,
        "url": url,
        "metric_label": metric["label"] if metric else "",
        "metric_value": metric["value"] if metric else "",
        "is_available": bool(url),
    }


def _workflow_steps(user, flags, sections):
    journal_metric = _find_metric(sections, "启用子期刊")
    article_metric = _find_metric(sections, f"近 {RECENT_DAYS} 天编辑", "我的草稿")
    review_metric = _find_metric(sections, "待审核")
    placement_metric = _find_metric(
        sections,
        "超容量版位",
        "启用主站版位",
    )
    publish_metric = _find_metric(
        sections,
        f"近 {RECENT_DAYS} 天发布",
        "等待发布",
        "当前活动版本",
        "活动版本",
    )
    can_journals = user.is_superuser or user.has_perm("site_settings.access_journals")
    can_articles = user.is_superuser or user.has_perm("site_settings.access_articles")
    can_review = user.is_superuser or user.has_perm(
        "site_settings.access_article_review"
    )
    can_placements = user.is_superuser or user.has_perm(
        "site_settings.access_placements"
    )
    can_publish = user.is_superuser or user.has_perm(
        "site_settings.access_static_publish"
    )

    return [
        _step(
            1,
            "子期刊",
            "先确定内容归属、栏目和期刊资料。",
            url=reverse("journals:index") if can_journals else "",
            metric=journal_metric,
        ),
        _step(
            2,
            "文章",
            "文章归属子期刊，但不会因此自动展示。",
            url=reverse("article_admin:index") if can_articles else "",
            metric=article_metric,
        ),
        _step(
            3,
            "审核",
            "审核通过只代表文章具备投放资格。",
            url=reverse("article_admin:pending") if can_review else "",
            metric=review_metric,
        ),
        _step(
            4,
            "投放",
            "选择目标页面、固定版位、排序和生效时间。",
            url=reverse("placements:index") if can_placements else "",
            metric=placement_metric,
        ),
        _step(
            5,
            "静态发布",
            "构建固定 HTML，并通过 manifest 发布或回滚。",
            url=reverse("static_publish:center") if can_publish else "",
            metric=publish_metric,
        ),
    ]


def _workspace_card(code, title, description, links):
    return {
        "code": code,
        "title": title,
        "description": description,
        "links": [link for link in links if link],
    }


def _workspace_link(label, url, description=""):
    return {"label": label, "url": url, "description": description}


def _workspace_cards(user, flags):
    cards = []
    can_journals = user.is_superuser or user.has_perm("site_settings.access_journals")
    if can_journals:
        cards.append(
            _workspace_card(
                "journals",
                "按子期刊工作",
                "以一个子期刊为主对象，再进入它的资料、栏目、文章、投放和静态主页。",
                [
                    _workspace_link("选择子期刊", reverse("journals:index")),
                    (
                        _workspace_link(
                            "\u680f\u76ee\u7ba1\u7406",
                            reverse("journals_category_admin"),
                        )
                        if flags["can_view_journal_categories"]
                        else None
                    ),
                    (
                        _workspace_link(
                            "\u6279\u91cf\u5bfc\u5165",
                            reverse("journals_import_dashboard"),
                        )
                        if flags["can_import_journals"]
                        else None
                    ),
                ],
            )
        )
    can_articles = user.is_superuser or user.has_perm("site_settings.access_articles")
    can_review = user.is_superuser or user.has_perm(
        "site_settings.access_article_review"
    )
    if can_articles or can_review:
        cards.append(
            _workspace_card(
                "articles",
                "按文章工作",
                "正文、主属期刊和审核状态属于文章；前台出现位置由投放单独控制。",
                [
                    (
                        _workspace_link("文章管理", reverse("article_admin:index"))
                        if can_articles
                        else None
                    ),
                    (
                        _workspace_link("待审核", reverse("article_admin:pending"))
                        if can_review
                        else None
                    ),
                ],
            )
        )
    can_placements = user.is_superuser or user.has_perm(
        "site_settings.access_placements"
    )
    can_slots = user.is_superuser or user.has_perm("site_settings.access_slots")
    if can_placements or can_slots:
        cards.append(
            _workspace_card(
                "delivery",
                "按编排目标工作",
                "在主站、栏目或子期刊的固定版位中配置文章、排序、置顶和时间。",
                [
                    (
                        _workspace_link("投放管理", reverse("placements:index"))
                        if can_placements
                        else None
                    ),
                    (
                        _workspace_link("版位管理", reverse("layout-slots:index"))
                        if can_slots
                        else None
                    ),
                ],
            )
        )
    can_publish = user.is_superuser or user.has_perm(
        "site_settings.access_static_publish"
    )
    if can_publish:
        cards.append(
            _workspace_card(
                "publishing",
                "按发布版本工作",
                "查看逐页面结果、失败重试、活动 manifest 和可回滚版本。",
                [
                    _workspace_link("静态发布中心", reverse("static_publish:center")),
                    (
                        _workspace_link("审计日志", reverse("auditlog:index"))
                        if flags["can_view_audit_log"]
                        else None
                    ),
                ],
            )
        )
    return cards


def _quick_actions(flags):
    actions = []
    if flags["can_edit_article"]:
        actions.append(
            _workspace_link(
                "进入文章管理",
                reverse("article_admin:index"),
                "新建、继续编辑或提交文章。",
            )
        )
    if flags["can_review_article"]:
        actions.append(
            _workspace_link(
                "处理待审核文章",
                reverse("article_admin:pending"),
                "查看正文差异并记录审核意见。",
            )
        )
    if flags["can_import_journals"]:
        actions.append(
            _workspace_link(
                "批量导入子期刊",
                reverse("journals_import_dashboard"),
                "先预览校验，再确认导入。",
            )
        )
    if flags["can_manage_placement"]:
        actions.append(
            _workspace_link(
                "配置文章投放",
                reverse("placements:index"),
                "仅审核通过的文章可进入受控版位。",
            )
        )
    if flags["can_publish_static"]:
        actions.append(
            _workspace_link(
                "进入静态发布",
                reverse("static_publish:center"),
                "构建、发布并检查逐页面结果。",
            )
        )
    return actions


def get_role_dashboard_context(user):
    flags = get_admin_permission_context(user)
    sections = []

    if flags["can_edit_article"]:
        sections.append(_content_section(user))
    if flags["can_review_article"]:
        sections.append(_review_section(user))
    if flags["can_change_journal"] or flags["can_manage_placement"]:
        operations = _operations_section(flags)
        if operations["metrics"]:
            sections.append(operations)
    if any(
        flags[name]
        for name in (
            "can_publish_static",
            "can_retry_publish",
            "can_rollback_publish",
        )
    ):
        sections.append(_publishing_section())
    if flags["is_readonly_dashboard"]:
        readonly = _readonly_section(flags)
        if readonly["metrics"]:
            sections.append(readonly)

    workflow_steps = _workflow_steps(user, flags, sections)
    workspace_cards = _workspace_cards(user, flags)
    quick_actions = _quick_actions(flags)
    return {
        "admin_permission_flags": flags,
        "workflow_steps": workflow_steps,
        "workspace_cards": workspace_cards,
        "quick_actions": quick_actions,
        "dashboard_sections": sections,
        "dashboard_has_content": bool(sections or workspace_cards or quick_actions),
    }


def should_show_role_dashboard(user):
    return get_admin_permission_context(user)["has_dashboard_access"]


class RoleDashboardPanel(Component):
    name = "role_dashboard"
    order = 10
    template_name = "wagtailadmin/home/role_dashboard.html"

    def get_context_data(self, parent_context):
        context = super().get_context_data(parent_context)
        context.update(get_role_dashboard_context(parent_context["request"].user))
        return context

    def render_html(self, parent_context=None):
        if not parent_context or not should_show_role_dashboard(
            parent_context["request"].user
        ):
            return ""
        return super().render_html(parent_context)
