from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import Journal
from ai_author_forum.site_settings.access_control import is_super_admin

from .admin_forms import (
    ArticleInteractionPolicyForm,
    JournalInteractionPolicyForm,
    require_reason,
)
from .models import ArticleInteractionPolicy, JournalInteractionPolicy
from .moderation_views import moderation_action, moderation_batch, moderation_index
from .permissions import accessible_journals, can_manage_policy
from .services import (
    StalePolicy,
    policy_status,
    update_article_policy,
    update_journal_policy,
)


class ReaderPolicyMenuItem(MenuItem):
    def is_shown(self, request):
        if is_super_admin(request.user):
            return True
        return accessible_journals(request.user).exists()


def _require_journal(request, journal):
    if not can_manage_policy(request.user, journal):
        raise PermissionDenied("无权访问该期刊互动政策。")


def policy_index(request):
    journals = accessible_journals(
        request.user, Journal.objects.filter(status="active")
    )
    rows = []
    for journal in journals.select_related("interaction_policy"):
        articles = ArticlePage.objects.filter(primary_journal=journal).select_related(
            "primary_journal"
        )
        rows.append(
            {
                "journal": journal,
                "policy": getattr(journal, "interaction_policy", None),
                "articles": [
                    {"article": article, "status": policy_status(article)}
                    for article in articles.order_by("title")[:200]
                ],
            }
        )
    return render(
        request,
        "wagtailadmin/reader_access/policy_index.html",
        {"title": "读者互动政策", "rows": rows},
    )


def journal_policy_edit(request, journal_id):
    journal = get_object_or_404(Journal, pk=journal_id)
    _require_journal(request, journal)
    policy = JournalInteractionPolicy.objects.filter(journal=journal).first()
    if request.method == "POST":
        form = JournalInteractionPolicyForm(request.POST)
        if is_super_admin(request.user):
            require_reason(form)
        if form.is_valid():
            try:
                update_journal_policy(
                    actor=request.user,
                    journal=journal,
                    expected_version=form.cleaned_data["expected_version"],
                    comments_mode=form.cleaned_data["default_comments_mode"],
                    download_enabled=form.cleaned_data["default_pdf_download_enabled"],
                    reason=form.cleaned_data.get("reason", ""),
                )
            except (StalePolicy, ValidationError) as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "期刊互动政策已保存，能力投影正在应用。")
                return redirect("reader_access_policy_index")
    else:
        form = JournalInteractionPolicyForm(
            initial={
                "default_comments_mode": (
                    policy.default_comments_mode
                    if policy
                    else JournalInteractionPolicy.CommentsMode.OPEN
                ),
                "default_pdf_download_enabled": (
                    policy.default_pdf_download_enabled if policy else True
                ),
                "expected_version": policy.version if policy else 0,
            }
        )
        if is_super_admin(request.user):
            require_reason(form)
    return render(
        request,
        "wagtailadmin/reader_access/policy_form.html",
        {
            "title": f"编辑期刊政策：{journal}",
            "object": journal,
            "form": form,
            "scope": "journal",
        },
    )


def article_policy_edit(request, article_id):
    article = get_object_or_404(
        ArticlePage.objects.select_related("primary_journal"), pk=article_id
    )
    _require_journal(request, article.primary_journal)
    policy = ArticleInteractionPolicy.objects.filter(article=article).first()
    if request.method == "POST":
        form = ArticleInteractionPolicyForm(request.POST)
        if is_super_admin(request.user):
            require_reason(form)
        if form.is_valid():
            try:
                update_article_policy(
                    actor=request.user,
                    article=article,
                    expected_version=form.cleaned_data["expected_version"],
                    comments_policy=form.cleaned_data["comments_policy"],
                    pdf_download_policy=form.cleaned_data["pdf_download_policy"],
                    reason=form.cleaned_data.get("reason", ""),
                )
            except (StalePolicy, ValidationError) as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "文章互动政策已保存，能力投影正在应用。")
                return redirect("reader_access_policy_index")
    else:
        form = ArticleInteractionPolicyForm(
            initial={
                "comments_policy": (
                    policy.comments_policy
                    if policy
                    else ArticleInteractionPolicy.CommentsPolicy.INHERIT
                ),
                "pdf_download_policy": (
                    policy.pdf_download_policy
                    if policy
                    else ArticleInteractionPolicy.PdfDownloadPolicy.INHERIT
                ),
                "expected_version": policy.version if policy else 0,
            }
        )
        if is_super_admin(request.user):
            require_reason(form)
    return render(
        request,
        "wagtailadmin/reader_access/policy_form.html",
        {
            "title": f"编辑文章政策：{article.title}",
            "object": article,
            "form": form,
            "scope": "article",
        },
    )


@hooks.register("register_admin_urls")
def register_admin_urls():
    return [
        path(
            "reader-access/moderation/",
            moderation_index,
            name="reader_access_moderation_index",
        ),
        path(
            "reader-access/moderation/comments/<uuid:comment_public_id>/action/",
            moderation_action,
            name="reader_access_moderation_action",
        ),
        path(
            "reader-access/moderation/batch/",
            moderation_batch,
            name="reader_access_moderation_batch",
        ),
        path(
            "reader-access/policies/", policy_index, name="reader_access_policy_index"
        ),
        path(
            "reader-access/policies/journals/<int:journal_id>/",
            journal_policy_edit,
            name="reader_access_journal_policy_edit",
        ),
        path(
            "reader-access/policies/articles/<int:article_id>/",
            article_policy_edit,
            name="reader_access_article_policy_edit",
        ),
    ]


@hooks.register("register_admin_menu_item")
def register_admin_menu_item():
    return ReaderPolicyMenuItem(
        "读者互动政策",
        reverse("reader_access_policy_index"),
        icon_name="cog",
        order=220,
    )


class ReaderModerationMenuItem(MenuItem):
    def is_shown(self, request):
        return (
            is_super_admin(request.user) or accessible_journals(request.user).exists()
        )


@hooks.register("register_admin_menu_item")
def register_moderation_menu_item():
    return ReaderModerationMenuItem(
        "读者评论审核",
        reverse("reader_access_moderation_index"),
        icon_name="warning",
        order=221,
    )
