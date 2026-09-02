from django.conf import settings
from django.http import Http404, HttpResponsePermanentRedirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET
from wagtail.views import serve as wagtail_serve

from ai_author_forum.articles.category_services import get_articles_for_category
from ai_author_forum.journals.category_services import get_category_navigation
from ai_author_forum.journals.models import (
    JournalCategory,
    JournalCategoryPathRedirect,
    JournalCategoryStatus,
)
from ai_author_forum.site_settings.models import NavigationItemPathRedirect
from ai_author_forum.site_settings.navigation import get_navigation_context
from ai_author_forum.static_publish.frontend import (
    get_content_column_context,
    get_current_issue_context,
    get_issue_archive_context,
    get_issue_detail_context,
    get_journal_index_context,
    get_journal_page_context,
    get_managed_navigation_info_context,
    get_section_page_context,
    get_static_article_context,
    get_static_info_page_context,
    get_static_search_context,
)


@require_GET
def journal_index(request):
    return TemplateResponse(
        request,
        "journals/journal_index.html",
        get_journal_index_context(),
    )


@require_GET
def journal_detail(request, slug):
    return TemplateResponse(
        request,
        "journals/journal_detail.html",
        get_journal_page_context(slug),
    )


@require_GET
def section_detail(request, section):
    return TemplateResponse(
        request,
        "sections/section_detail.html",
        get_section_page_context(section),
    )


@require_GET
def static_info_page(request, group_slug, page_slug):
    return TemplateResponse(
        request,
        "pages/static_info_page.html",
        get_static_info_page_context(group_slug, page_slug),
    )


@require_GET
def managed_navigation_info(request, internal_path, journal_slug=None):
    try:
        context = get_managed_navigation_info_context(
            internal_path=internal_path,
            journal_slug=journal_slug,
        )
    except Http404:
        # This constrained route precedes Wagtail's catch-all so it can render
        # active NavigationItem internal paths. Let real Wagtail pages keep
        # ownership of the same URL when no managed navigation item matches.
        fallback_path = "/".join(
            part for part in (journal_slug, str(internal_path).strip("/")) if part
        )
        return wagtail_serve(request, fallback_path)
    return TemplateResponse(request, "pages/static_info_page.html", context)


@require_GET
def content_column_detail(
    request,
    column_slug,
    journal_slug=None,
    page_number=1,
    article_type="",
    year=None,
    paginated=False,
):
    context = get_content_column_context(
        column_slug=column_slug,
        journal_slug=journal_slug,
        page_number=page_number,
        article_type=article_type,
        year=year,
    )
    if paginated and page_number == 1:
        return HttpResponsePermanentRedirect(context["all_articles_url"])
    template = {
        "research_list": "sections/research_list.html",
        "news_landing": "sections/news_landing.html",
        "chronological": "sections/chronological_list.html",
    }[context["column_config"].template_variant]
    return TemplateResponse(request, template, context)


@require_GET
def legacy_navigation_redirect(request, section):
    old_path = f"/explore-content/{section}/"
    try:
        redirect = NavigationItemPathRedirect.objects.get(
            old_path=old_path,
            is_active=True,
        )
    except NavigationItemPathRedirect.DoesNotExist as exc:
        raise Http404("Navigation redirect not found") from exc
    return HttpResponsePermanentRedirect(redirect.new_path)


@require_GET
def current_issue(request, journal_slug=None):
    return TemplateResponse(
        request,
        "journals/current_issue.html",
        get_current_issue_context(journal_slug),
    )


@require_GET
def issue_archive(request, journal_slug=None):
    return TemplateResponse(
        request,
        "journals/issue_archive.html",
        get_issue_archive_context(journal_slug),
    )


@require_GET
def issue_detail(request, issue_slug, journal_slug=None):
    return TemplateResponse(
        request,
        "journals/issue_detail.html",
        get_issue_detail_context(
            issue_slug=issue_slug,
            journal_slug=journal_slug,
        ),
    )


@require_GET
def article_detail(request, slug):
    return TemplateResponse(
        request,
        "articles/article_page.html",
        get_static_article_context(slug),
    )


@require_GET
def static_search(request):
    return TemplateResponse(
        request,
        "search/static_recommendations.html",
        get_static_search_context(),
    )


@require_GET
def journal_category_detail(
    request, journal_slug, category_path, page_number=1, paginated=False
):
    normalized = category_path.strip().strip("/")
    category = (
        JournalCategory.objects.select_related("journal", "parent")
        .filter(journal__slug=journal_slug, path_cache=normalized)
        .first()
    )
    if category is None:
        redirect = (
            JournalCategoryPathRedirect.objects.filter(
                journal__slug=journal_slug,
                old_path=request.path,
                is_active=True,
            )
            .select_related("journal")
            .first()
        )
        if redirect:
            return HttpResponsePermanentRedirect(redirect.new_path)
        raise Http404("Category not found")
    if category.status in {
        JournalCategoryStatus.DISABLED,
        JournalCategoryStatus.ARCHIVED,
    }:
        raise Http404("Category not available")
    if paginated and page_number == 1:
        return HttpResponsePermanentRedirect(category.get_absolute_url())
    query_page = request.GET.get("page")
    if not paginated and query_page not in (None, "", "1"):
        try:
            requested_page = int(query_page)
        except (TypeError, ValueError):
            raise Http404("Invalid category page") from None
        return HttpResponsePermanentRedirect(
            f"{category.get_absolute_url()}page/{requested_page}/"
        )
    if page_number < 1:
        raise Http404("Invalid category page")
    page_size = int(getattr(settings, "STATIC_CATEGORY_PAGE_SIZE", 20))
    articles = get_articles_for_category(
        category=category,
        page=page_number,
        page_size=page_size,
        include_active_release=True,
    )
    if page_number > articles.paginator.num_pages:
        raise Http404("Category page not found")
    return TemplateResponse(
        request,
        "journals/category_detail.html",
        {
            "journal": category.journal,
            "category": category,
            "ancestors": category.get_ancestors(),
            "articles": articles,
            "category_navigation": get_category_navigation(journal=category.journal),
            "page_title": category.seo_title or category.name,
            "managed_navigation": get_navigation_context(
                journal=category.journal,
                current_path=category.get_absolute_url(),
                strict=True,
            ),
        },
    )
