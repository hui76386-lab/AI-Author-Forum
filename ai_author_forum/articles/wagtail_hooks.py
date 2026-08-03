from django.contrib import messages
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.http import JsonResponse
from django.templatetags.static import static
from django.urls import path, reverse, reverse_lazy
from django.utils.html import format_html
from wagtail import hooks
from wagtail.admin.action_menu import ActionMenuItem
from wagtail.admin.auth import permission_denied, permission_required
from wagtail.admin.menu import MenuItem
from wagtail.admin.viewsets.base import ViewSet
from wagtail.models import (
    GroupPagePermission,
    Page,
    Workflow,
    WorkflowContentType,
    WorkflowPage,
    WorkflowTask,
)
from wagtail.signals import workflow_approved, workflow_rejected, workflow_submitted

from ai_author_forum.journals.models import JournalCategory, JournalCategoryStatus

from .import_views import (
    article_import_confirm,
    article_import_dashboard,
    article_import_errors,
    article_import_status,
    article_import_template,
)
from .integrations import log_article_audit
from .models import (
    ARTICLE_EDIT_PERMISSION,
    ARTICLE_PLACEMENT_PERMISSION,
    ARTICLE_RAW_HTML_PERMISSION,
    ARTICLE_REVIEW_PERMISSION,
    WAGTAIL_ADMIN_ACCESS_PERMISSION,
    ArticlePage,
    ArticleReviewRecord,
    ArticleReviewTask,
    user_has_article_edit_permission,
)
from .views import (
    ArticleEditorCapabilitiesView,
    ArticleListView,
    ArticleReviewDetailView,
    ArticleReviewPreviewView,
    ArticleSubmitReviewView,
    BulkArticleActionView,
    PendingArticleListView,
    user_can_review_articles,
)

ARTICLE_MODERATION_WORKFLOW_NAME = "Article Moderation"
ARTICLE_REVIEW_TASK_NAME = "Article Review"
CONTENT_EDITORS_GROUP_NAME = "Content Editors"
CONTENT_REVIEWERS_GROUP_NAME = "审核人员"
LEGACY_CONTENT_REVIEWERS_GROUP_NAME = "Content Reviewers"
CONTENT_PUBLISHERS_GROUP_NAME = "Content Publishers"
ARTICLE_PERMISSION_NAMES = {
    ARTICLE_EDIT_PERMISSION.split(".", maxsplit=1)[1]: "可编辑文章",
    ARTICLE_REVIEW_PERMISSION.split(".", maxsplit=1)[1]: "可审核文章",
    ARTICLE_PLACEMENT_PERMISSION.split(".", maxsplit=1)[1]: "可管理文章投放",
    ARTICLE_RAW_HTML_PERMISSION.split(".", maxsplit=1)[1]: (
        "可使用文章 Raw HTML 正文块"
    ),
}
REVIEW_PERMISSION_CODENAME = ARTICLE_REVIEW_PERMISSION.split(".", maxsplit=1)[1]
EDIT_PERMISSION_CODENAME = ARTICLE_EDIT_PERMISSION.split(".", maxsplit=1)[1]
PLACEMENT_PERMISSION_CODENAME = ARTICLE_PLACEMENT_PERMISSION.split(".", maxsplit=1)[1]
RAW_HTML_PERMISSION_CODENAME = ARTICLE_RAW_HTML_PERMISSION.split(".", maxsplit=1)[1]


class ArticleAdminViewSet(ViewSet):
    name = "article_admin"
    url_prefix = "articles"
    url_namespace = "article_admin"

    def get_urlpatterns(self):
        require_edit_permission = permission_required("site_settings.access_articles")
        require_review_permission = permission_required(ARTICLE_REVIEW_PERMISSION)
        return [
            path("import/", article_import_dashboard, name="import"),
            path("import/confirm/", article_import_confirm, name="import_confirm"),
            path("import/status/", article_import_status, name="import_status"),
            path("import/template/", article_import_template, name="import_template"),
            path(
                "import/errors/<int:job_id>/",
                article_import_errors,
                name="import_errors",
            ),
            path(
                "",
                require_edit_permission(ArticleListView.as_view()),
                name="index",
            ),
            path(
                "pending/",
                require_review_permission(PendingArticleListView.as_view()),
                name="pending",
            ),
            path(
                "<int:page_id>/review/",
                require_review_permission(ArticleReviewDetailView.as_view()),
                name="review_detail",
            ),
            path(
                "<int:page_id>/review/preview/",
                require_review_permission(ArticleReviewPreviewView.as_view()),
                name="review_preview",
            ),
            path(
                "<int:page_id>/submit-review/",
                permission_required(ARTICLE_EDIT_PERMISSION)(
                    ArticleSubmitReviewView.as_view()
                ),
                name="submit_review",
            ),
            path(
                "category-options/",
                require_edit_permission(article_category_options),
                name="category_options",
            ),
            path(
                "bulk-action/",
                permission_required(WAGTAIL_ADMIN_ACCESS_PERMISSION)(
                    BulkArticleActionView.as_view()
                ),
                name="bulk_action",
            ),
            path(
                "editor-capabilities/",
                permission_required(WAGTAIL_ADMIN_ACCESS_PERMISSION)(
                    ArticleEditorCapabilitiesView.as_view()
                ),
                name="editor_capabilities",
            ),
        ]


class ArticleListMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "site_settings.access_articles"
        )


class ArticleReviewMenuItem(MenuItem):
    def is_shown(self, request):
        return user_can_review_articles(request.user)


class PlacementSyncExceptionMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser or (
            request.user.has_perm("site_settings.access_placements")
            and request.user.has_perm("placements.view_system_categoryplacement")
        )


def article_category_options(request):
    journal_id = request.GET.get("journal")
    categories = JournalCategory.objects.none()
    if journal_id:
        categories = JournalCategory.objects.filter(
            journal_id=journal_id,
            status__in=(JournalCategoryStatus.ACTIVE, JournalCategoryStatus.HIDDEN),
        ).order_by("path_cache")
    return JsonResponse(
        {
            "categories": [
                {
                    "id": item.pk,
                    "label": f"{item.full_path} [{item.code}]",
                    "status": item.status,
                }
                for item in categories
            ]
        }
    )


@hooks.register("register_admin_viewset")
def register_article_admin_viewset():
    return ArticleAdminViewSet()


@hooks.register("register_admin_menu_item")
def register_all_articles_menu_item():
    return ArticleListMenuItem(
        "所有文章",
        reverse_lazy("article_admin:index"),
        name="all-articles",
        icon_name="doc-full",
        order=300,
    )


@hooks.register("register_admin_menu_item")
def register_pending_articles_menu_item():
    return ArticleReviewMenuItem(
        "待审核",
        reverse_lazy("article_admin:pending"),
        name="pending-articles",
        icon_name="list-ul",
        order=301,
    )


@hooks.register("register_admin_menu_item")
def register_placement_sync_exception_menu_item():
    return PlacementSyncExceptionMenuItem(
        "投放同步异常",
        f"{reverse_lazy('system-category-placements:index')}?errors=1",
        name="placement-sync-exceptions",
        icon_name="warning",
        order=303,
    )


@hooks.register("insert_editor_js")
def article_category_selector_js():
    endpoint = reverse("article_admin:category_options")
    return format_html(
        """<script>
(function() {{
  const journalSelector = '[name$="primary_journal"]';
  const categorySelector = 'select[name$="category"]';
  let currentJournalId = null;
  let categoryOptions = [];
  let refreshRequest = null;

  function categorySelects() {{
    return Array.from(document.querySelectorAll(categorySelector));
  }}

  function renderCategoryOptions(clearExisting) {{
    categorySelects().forEach((node) => {{
      const selected = clearExisting ? '' : node.value;
      node.replaceChildren(new Option('---------', ''));
      categoryOptions.forEach((item) => {{
        node.add(new Option(item.label, item.id, false, String(item.id) === selected));
      }});
      if (currentJournalId && !categoryOptions.length) {{
        node.add(new Option('No selectable category is configured for this journal. Create one in Category management first.', ''));
      }}
      node.disabled = !currentJournalId || !categoryOptions.length;
    }});
  }}

  async function refresh(clearExisting) {{
    const journal = document.querySelector(journalSelector);
    const journalId = journal ? journal.value : '';
    if (!journalId) {{
      currentJournalId = '';
      categoryOptions = [];
      renderCategoryOptions(true);
      return;
    }}
    if (journalId === currentJournalId) {{
      renderCategoryOptions(clearExisting);
      return;
    }}

    currentJournalId = journalId;
    refreshRequest = journalId;
    try {{
      const response = await fetch('{0}?journal=' + encodeURIComponent(journalId), {{credentials: 'same-origin'}});
      if (!response.ok || refreshRequest !== journalId) return;
      const payload = await response.json();
      if (refreshRequest !== journalId) return;
      categoryOptions = Array.isArray(payload.categories) ? payload.categories : [];
      renderCategoryOptions(clearExisting);
    }} catch (error) {{
      if (refreshRequest === journalId) {{
        categoryOptions = [];
        renderCategoryOptions(clearExisting);
      }}
    }}
  }}

  function initialise() {{
    refresh(false);
    document.addEventListener('change', (event) => {{
      if (event.target.matches(journalSelector)) {{
        currentJournalId = null;
        refresh(true);
      }}
    }});
    new MutationObserver((mutations) => {{
      const hasNewCategorySelect = mutations.some((mutation) =>
        Array.from(mutation.addedNodes).some((node) =>
          node.nodeType === Node.ELEMENT_NODE &&
          (node.matches?.(categorySelector) || node.querySelector?.(categorySelector))
        )
      );
      if (hasNewCategorySelect) renderCategoryOptions(false);
    }}).observe(document.body, {{childList: true, subtree: true}});
  }}

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initialise, {{once: true}});
  }} else {{
    initialise();
  }}
}})();
</script>""",
        endpoint,
    )


@hooks.register("construct_page_action_menu")
def remove_article_direct_workflow_submit_action(menu_items, request, context):
    page = context.get("page")
    article = getattr(page, "specific", page)
    if isinstance(article, ArticlePage):
        menu_items[:] = [
            item for item in menu_items if getattr(item, "name", "") != "action-submit"
        ]


@hooks.register("insert_editor_js")
def article_editor_protection_js():
    return format_html(
        '<script src="{}" defer data-capabilities-url="{}"></script>',
        static("articles/js/editor-protection.js"),
        reverse("article_admin:editor_capabilities"),
    )


@hooks.register("insert_global_admin_css")
def article_admin_css():
    return format_html(
        '<link rel="stylesheet" href="{}">',
        static("articles/css/admin-maturity.css"),
    )


class ArticleSaveActionMenuItem(ActionMenuItem):
    template_name = "wagtailadmin/articles/action_menu_item.html"
    icon_name = "draft"

    def __init__(self, *, name, label, value, order):
        super().__init__(order=order)
        self.name = name
        self.label = label
        self.value = value

    def is_shown(self, context):
        if context.get("view") == "create":
            resolver_match = getattr(context.get("request"), "resolver_match", None)
            route_kwargs = getattr(resolver_match, "kwargs", {}) or {}
            is_article_create = (
                route_kwargs.get("content_type_app_name") == ArticlePage._meta.app_label
                and route_kwargs.get("content_type_model_name")
                == ArticlePage._meta.model_name
            )
            return is_article_create and super().is_shown(context)

        page = context.get("page")
        specific_class = getattr(page, "specific_class", None)
        return specific_class is ArticlePage and super().is_shown(context)

    def get_context_data(self, parent_context):
        context = super().get_context_data(parent_context)
        context["value"] = self.value
        return context


@hooks.register("register_page_action_menu_item")
def register_article_save_return_action():
    return ArticleSaveActionMenuItem(
        name="article-save-return",
        label="保存草稿并返回列表",
        value="article-return-list",
        order=5,
    )


@hooks.register("register_page_action_menu_item")
def register_article_save_continue_action():
    return ArticleSaveActionMenuItem(
        name="article-save-continue",
        label="保存后继续编辑",
        value="article-continue",
        order=6,
    )


@hooks.register("register_page_action_menu_item")
def register_article_save_new_action():
    return ArticleSaveActionMenuItem(
        name="article-save-new",
        label="保存并新建",
        value="article-new",
        order=7,
    )


def _redirect_after_article_quick_save(request, page):
    if not isinstance(page.specific, ArticlePage):
        return None
    save_destination = request.POST.get("action-save")
    if save_destination == "article-return-list":
        from django.shortcuts import redirect

        return redirect("article_admin:index")
    if save_destination == "article-new":
        from django.shortcuts import redirect

        return redirect(
            "wagtailadmin_pages:add",
            ArticlePage._meta.app_label,
            ArticlePage._meta.model_name,
            page.get_parent().pk,
        )
    return None


@hooks.register("after_edit_page")
def redirect_after_article_quick_save(request, page):
    return _redirect_after_article_quick_save(request, page)


@hooks.register("after_create_page")
def redirect_after_article_quick_save_create(request, page):
    return _redirect_after_article_quick_save(request, page)


@hooks.register("before_create_page")
def require_article_edit_permission_before_create(request, parent_page, page_class):
    if page_class is not ArticlePage:
        return None

    if not user_has_article_edit_permission(request.user):
        return permission_denied(request)

    return None


@hooks.register("before_edit_page")
def require_article_edit_permission_before_edit(request, page):
    article = page.specific
    if not isinstance(article, ArticlePage):
        return None

    try:
        article._raise_if_user_cannot_save(request.user)
    except PermissionDenied:
        return permission_denied(request)

    return None


@hooks.register("before_submit_page")
def validate_article_categories_before_submit(request, page):
    article = page.specific
    if not isinstance(article, ArticlePage):
        return None

    from .category_services import (
        ArticleCategoryError,
        validate_article_category_revision,
    )

    try:
        validate_article_category_revision(article=article, action="submit")
    except ArticleCategoryError as exc:
        messages.error(request, f"{exc.code}: {exc.message}")
        return permission_denied(request)
    return None


@hooks.register("after_publish_page")
def synchronize_category_placements_after_publish(request, page):
    article = page.specific
    if not isinstance(article, ArticlePage):
        return None
    revision_id = getattr(article, "live_revision_id", None)
    actor_id = getattr(getattr(request, "user", None), "pk", None)
    transaction.on_commit(
        lambda article_id=article.pk, revision_id=revision_id, actor_id=actor_id: _run_category_sync(
            article_id, revision_id, actor_id
        )
    )
    return None


@hooks.register("after_unpublish_page")
def disable_category_placements_after_unpublish(request, page):
    article = page.specific
    if not isinstance(article, ArticlePage):
        return None
    actor_id = getattr(getattr(request, "user", None), "pk", None)
    transaction.on_commit(
        lambda article_id=article.pk, actor_id=actor_id: _run_category_disable(
            article_id, actor_id
        )
    )
    return None


@hooks.register("register_permissions")
def register_article_permissions():
    article_page_content_type = ContentType.objects.get_for_model(
        ArticlePage,
        for_concrete_model=False,
    )
    return Permission.objects.filter(
        content_type=article_page_content_type,
        codename__in=ARTICLE_PERMISSION_NAMES,
    )


@receiver(post_migrate, dispatch_uid="articles.ensure_article_moderation_workflow")
def ensure_article_moderation_workflow(sender, **kwargs):
    if sender.label != "articles":
        return

    try:
        with transaction.atomic():
            workflow = _get_or_create_article_workflow()
            _assign_workflow_to_existing_article_pages(workflow)
    except (OperationalError, ProgrammingError):
        return


@hooks.register("after_create_page")
def assign_article_workflow_after_create(request, page):
    article = page.specific
    if not isinstance(article, ArticlePage):
        return None

    workflow = _get_or_create_article_workflow()
    WorkflowPage.objects.update_or_create(
        page=article,
        defaults={"workflow": workflow},
    )

    return None


@receiver(workflow_submitted, dispatch_uid="articles.workflow_submitted")
def sync_article_status_on_workflow_submitted(sender, instance, user, **kwargs):
    _sync_article_review_status(
        workflow_state=instance,
        status=ArticlePage.ReviewStatus.SUBMITTED,
        user=user,
        default_comment="已提交至文章审核流程。",
    )


@receiver(workflow_approved, dispatch_uid="articles.workflow_approved")
def sync_article_status_on_workflow_approved(sender, instance, user, **kwargs):
    article, revision = _sync_article_review_status(
        workflow_state=instance,
        status=ArticlePage.ReviewStatus.APPROVED,
        user=user,
        default_comment="文章审核流程已批准。",
    )
    if article:
        actor_id = getattr(user, "pk", None)
        revision_id = getattr(revision, "pk", None) or getattr(
            article, "live_revision_id", None
        )
        transaction.on_commit(
            lambda article_id=article.pk, revision_id=revision_id, actor_id=actor_id: _run_category_sync(
                article_id, revision_id, actor_id
            )
        )


@receiver(workflow_rejected, dispatch_uid="articles.workflow_rejected")
def sync_article_status_on_workflow_rejected(sender, instance, user, **kwargs):
    _sync_article_review_status(
        workflow_state=instance,
        status=ArticlePage.ReviewStatus.REJECTED,
        user=user,
        default_comment="文章审核流程已驳回。",
    )


def _get_or_create_article_workflow():
    groups = _ensure_article_permission_groups()
    reviewers_group = groups["reviewers"]

    task, _ = ArticleReviewTask.objects.get_or_create(
        name=ARTICLE_REVIEW_TASK_NAME,
        defaults={"active": True},
    )
    if not task.active:
        task.active = True
        task.save(update_fields=["active"])
    task.groups.set([reviewers_group])

    workflow, _ = Workflow.objects.get_or_create(
        name=ARTICLE_MODERATION_WORKFLOW_NAME,
        defaults={"active": True},
    )
    if not workflow.active:
        workflow.active = True
        workflow.save(update_fields=["active"])

    WorkflowTask.objects.update_or_create(
        workflow=workflow,
        task=task,
        defaults={"sort_order": 0},
    )

    article_page_content_type = ContentType.objects.get_for_model(
        ArticlePage,
        for_concrete_model=False,
    )
    WorkflowContentType.objects.update_or_create(
        content_type=article_page_content_type,
        defaults={"workflow": workflow},
    )

    return workflow


def _get_or_create_content_reviewers_group():
    group, _ = Group.objects.get_or_create(
        name=CONTENT_REVIEWERS_GROUP_NAME,
    )
    legacy_group = Group.objects.filter(
        name=LEGACY_CONTENT_REVIEWERS_GROUP_NAME
    ).first()
    if legacy_group is not None and legacy_group.pk != group.pk:
        group.user_set.add(*legacy_group.user_set.all())
        article_permissions = _get_or_create_article_permissions()
        legacy_group.permissions.remove(*article_permissions.values())
        _clear_wagtail_page_permissions(legacy_group)

    _set_django_permissions(
        group,
        add_codenames=[REVIEW_PERMISSION_CODENAME],
        remove_codenames=[
            EDIT_PERMISSION_CODENAME,
            PLACEMENT_PERMISSION_CODENAME,
            RAW_HTML_PERMISSION_CODENAME,
        ],
    )
    _clear_wagtail_page_permissions(group)
    return group


def _ensure_article_permission_groups():
    return {
        "editors": _get_or_create_content_editors_group(),
        "reviewers": _get_or_create_content_reviewers_group(),
        "publishers": _get_or_create_content_publishers_group(),
    }


def _get_or_create_content_editors_group():
    group, _ = Group.objects.get_or_create(name=CONTENT_EDITORS_GROUP_NAME)
    _set_django_permissions(
        group,
        add_codenames=[EDIT_PERMISSION_CODENAME],
        remove_codenames=[
            REVIEW_PERMISSION_CODENAME,
            PLACEMENT_PERMISSION_CODENAME,
            RAW_HTML_PERMISSION_CODENAME,
        ],
    )
    _set_wagtail_page_permissions(group, permission_types=["add"])
    return group


def _get_or_create_content_publishers_group():
    group, _ = Group.objects.get_or_create(name=CONTENT_PUBLISHERS_GROUP_NAME)
    _set_django_permissions(
        group,
        add_codenames=[PLACEMENT_PERMISSION_CODENAME],
        remove_codenames=[
            EDIT_PERMISSION_CODENAME,
            REVIEW_PERMISSION_CODENAME,
            RAW_HTML_PERMISSION_CODENAME,
        ],
    )
    _clear_wagtail_page_permissions(group)
    return group


def _set_django_permissions(group, add_codenames, remove_codenames):
    _add_wagtail_admin_access_permission(group)
    article_permissions = _get_or_create_article_permissions()

    group.permissions.add(
        *[
            article_permissions[codename]
            for codename in add_codenames
            if codename in article_permissions
        ]
    )
    group.permissions.remove(
        *[
            article_permissions[codename]
            for codename in remove_codenames
            if codename in article_permissions
        ]
    )


def _add_wagtail_admin_access_permission(group):
    access_admin_permission = Permission.objects.filter(
        content_type__app_label="wagtailadmin",
        codename="access_admin",
    ).first()

    if access_admin_permission:
        group.permissions.add(access_admin_permission)


def _get_or_create_article_permissions():
    article_page_content_type = ContentType.objects.get_for_model(
        ArticlePage,
        for_concrete_model=False,
    )
    permissions = {}

    for codename, name in ARTICLE_PERMISSION_NAMES.items():
        permission, _ = Permission.objects.update_or_create(
            content_type=article_page_content_type,
            codename=codename,
            defaults={"name": name},
        )
        permissions[codename] = permission

    return permissions


def _set_wagtail_page_permissions(group, permission_types):
    root_page = Page.get_first_root_node()
    permissions = _get_wagtail_page_permissions(permission_types)

    group.page_permissions.exclude(
        page=root_page,
        permission__in=permissions,
    ).delete()

    for permission in permissions:
        GroupPagePermission.objects.update_or_create(
            group=group,
            page=root_page,
            permission=permission,
        )


def _clear_wagtail_page_permissions(group):
    group.page_permissions.all().delete()


def _get_wagtail_page_permissions(permission_types):
    codenames = [f"{permission_type}_page" for permission_type in permission_types]
    return list(
        Permission.objects.filter(
            content_type__app_label="wagtailcore",
            codename__in=codenames,
        )
    )


def _assign_workflow_to_existing_article_pages(workflow):
    for article in ArticlePage.objects.all():
        WorkflowPage.objects.update_or_create(
            page=article,
            defaults={"workflow": workflow},
        )


def _sync_article_review_status(workflow_state, status, user, default_comment):
    article = _get_article_from_workflow_state(workflow_state)
    if not article:
        return None, None

    update_values = {"review_status": status}
    review_revision = _get_workflow_review_revision(
        workflow_state=workflow_state,
        article=article,
        status=status,
        user=user,
    )

    if status == ArticlePage.ReviewStatus.APPROVED:
        update_values["approved_version_id"] = (
            review_revision.pk if review_revision else None
        )
        if article.publication_status not in (
            ArticlePage.PublicationStatus.BUILT,
            ArticlePage.PublicationStatus.PUBLISHED,
        ):
            update_values["publication_status"] = ArticlePage.PublicationStatus.APPROVED
    elif status == ArticlePage.ReviewStatus.REJECTED:
        update_values["rejected_version_id"] = (
            review_revision.pk if review_revision else None
        )
        if article.publication_status:
            update_values["publication_status"] = ArticlePage.PublicationStatus.OFFLINE

    ArticlePage.objects.filter(pk=article.pk).update(**update_values)
    from .publication import sync_article_placement_status

    sync_article_placement_status(article.pk)

    if user:
        comment = _get_workflow_comment(workflow_state, default_comment)
        record = ArticleReviewRecord.objects.create(
            article=article,
            reviewer=user,
            action=status,
            revision=review_revision,
            comment=comment,
        )
        log_article_audit(
            action=status,
            article=article,
            user=user,
            comment=comment,
            metadata={
                "workflow_state_id": workflow_state.pk,
                "task_state_id": getattr(
                    workflow_state.current_task_state,
                    "pk",
                    None,
                ),
                "review_record_id": record.pk,
                "review_revision_id": review_revision.pk if review_revision else None,
            },
        )

    return article, review_revision


def _get_article_from_workflow_state(workflow_state):
    obj = workflow_state.content_object

    if hasattr(obj, "specific"):
        obj = obj.specific

    if isinstance(obj, ArticlePage):
        return obj

    return None


def _get_workflow_review_revision(workflow_state, article, status, user):
    if status not in (
        ArticlePage.ReviewStatus.APPROVED,
        ArticlePage.ReviewStatus.REJECTED,
    ):
        return None

    task_state = workflow_state.current_task_state
    if task_state and task_state.revision_id:
        return task_state.revision

    return article._get_or_create_current_review_revision(user)


def _get_workflow_comment(workflow_state, default_comment):
    task_state = workflow_state.current_task_state

    if task_state and task_state.comment:
        return task_state.comment

    return default_comment


def _actor_from_id(actor_id):
    if not actor_id:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _run_category_sync(article_id, revision_id, actor_id):
    from ai_author_forum.placements.category_services import sync_category_placements

    return sync_category_placements(
        article_id=article_id,
        revision_id=revision_id,
        actor=_actor_from_id(actor_id),
    )


def _run_category_disable(article_id, actor_id):
    from ai_author_forum.placements.category_services import disable_category_placements

    disable_category_placements(
        article_id=article_id,
        actor=_actor_from_id(actor_id),
    )


from .viewsets import ArticleReviewViewSet, ArticlesViewSet  # noqa: E402


@hooks.register("register_admin_viewset")
def register_foundation_article_viewsets():
    return [ArticlesViewSet(), ArticleReviewViewSet()]
