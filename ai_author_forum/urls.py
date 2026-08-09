from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import include, path, re_path
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import check_for_language
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

from ai_author_forum.news.feeds import LatestArticlesFeed
from ai_author_forum.static_publish import frontend_views, health_views


def legacy_language_set(request, language_code):
    # Compatibility endpoint for cached admin language links. Older pages
    # used /i18n/<language>/setlang/ as a GET URL, while Django's built-in
    # endpoint accepts POST only.
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "/"

    if not check_for_language(language_code):
        return HttpResponseRedirect(next_url)

    response = HttpResponseRedirect(next_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language_code,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


core_urlpatterns = [
    path("healthz/", health_views.healthz, name="healthz"),
    path("readyz/", health_views.readyz, name="readyz"),
    path("account/", include("ai_author_forum.users.account_urls")),
    path("django-admin/", admin.site.urls),
    path("admin/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path(
        "i18n/<str:language_code>/setlang/",
        legacy_language_set,
        name="legacy_admin_language_set",
    ),
    path("i18n/", include("django.conf.urls.i18n")),
    path("author/", include("ai_author_forum.articles.author_urls")),
]

frontend_urlpatterns = [
    path("journals/", frontend_views.journal_index, name="journal_index"),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/type/<slug:article_type>/year/<int:year>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="journal_content_column_type_year_page",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/type/<slug:article_type>/year/<int:year>/",
        frontend_views.content_column_detail,
        name="journal_content_column_type_year",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/type/<slug:article_type>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="journal_content_column_type_page",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/type/<slug:article_type>/",
        frontend_views.content_column_detail,
        name="journal_content_column_type",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/year/<int:year>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="journal_content_column_year_page",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/year/<int:year>/",
        frontend_views.content_column_detail,
        name="journal_content_column_year",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="journal_content_column_page",
    ),
    path(
        "journals/<slug:journal_slug>/sections/<slug:column_slug>/",
        frontend_views.content_column_detail,
        name="journal_content_column_detail",
    ),
    path(
        "journals/<slug:journal_slug>/current-issue/",
        frontend_views.current_issue,
        name="journal_current_issue",
    ),
    path(
        "journals/<slug:journal_slug>/issues/",
        frontend_views.issue_archive,
        name="journal_issue_archive",
    ),
    path(
        "journals/<slug:journal_slug>/issues/<slug:issue_slug>/",
        frontend_views.issue_detail,
        name="journal_issue_detail",
    ),
    path(
        "journals/<slug:journal_slug>/categories/<path:category_path>/page/<int:page_number>/",
        frontend_views.journal_category_detail,
        {"paginated": True},
        name="journal_category_page",
    ),
    path(
        "journals/<slug:journal_slug>/categories/<path:category_path>/",
        frontend_views.journal_category_detail,
        name="journal_category_detail",
    ),
    path(
        "journals/<slug:journal_slug>/<path:internal_path>/",
        frontend_views.managed_navigation_info,
        name="journal_managed_navigation_info",
    ),
    path("journals/<slug:slug>/", frontend_views.journal_detail, name="journal_detail"),
    path(
        "sections/<slug:column_slug>/type/<slug:article_type>/year/<int:year>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="main_content_column_type_year_page",
    ),
    path(
        "sections/<slug:column_slug>/type/<slug:article_type>/year/<int:year>/",
        frontend_views.content_column_detail,
        name="main_content_column_type_year",
    ),
    path(
        "sections/<slug:column_slug>/type/<slug:article_type>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="main_content_column_type_page",
    ),
    path(
        "sections/<slug:column_slug>/type/<slug:article_type>/",
        frontend_views.content_column_detail,
        name="main_content_column_type",
    ),
    path(
        "sections/<slug:column_slug>/year/<int:year>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="main_content_column_year_page",
    ),
    path(
        "sections/<slug:column_slug>/year/<int:year>/",
        frontend_views.content_column_detail,
        name="main_content_column_year",
    ),
    path(
        "sections/<slug:column_slug>/page/<int:page_number>/",
        frontend_views.content_column_detail,
        {"paginated": True},
        name="main_content_column_page",
    ),
    path(
        "sections/<slug:column_slug>/",
        frontend_views.content_column_detail,
        name="main_content_column_detail",
    ),
    path(
        "explore-content/current-issue/",
        frontend_views.current_issue,
        name="main_current_issue",
    ),
    path(
        "explore-content/browse-issues/",
        frontend_views.issue_archive,
        name="main_issue_archive",
    ),
    path(
        "issues/<slug:issue_slug>/",
        frontend_views.issue_detail,
        name="main_issue_detail",
    ),
    path(
        "explore-content/ai-article/",
        frontend_views.legacy_navigation_redirect,
        {"section": "ai-article"},
        name="legacy_ai_article",
    ),
    path(
        "explore-content/news/",
        frontend_views.legacy_navigation_redirect,
        {"section": "news"},
        name="legacy_news",
    ),
    path(
        "explore-content/opinion/",
        frontend_views.legacy_navigation_redirect,
        {"section": "opinion"},
        name="legacy_opinion",
    ),
    path(
        "explore-content/research-analysis/",
        frontend_views.legacy_navigation_redirect,
        {"section": "research-analysis"},
        name="legacy_research_analysis",
    ),
    path(
        "about-the-forum/<slug:page_slug>/",
        frontend_views.static_info_page,
        {"group_slug": "about-the-forum"},
        name="static_info_page",
    ),
    path(
        "co-authoring-with-ai/<slug:page_slug>/",
        frontend_views.static_info_page,
        {"group_slug": "co-authoring-with-ai"},
        name="static_info_page",
    ),
    path(
        "for-readers/<slug:page_slug>/",
        frontend_views.static_info_page,
        {"group_slug": "for-readers"},
        name="static_info_page",
    ),
    path("articles/<path:slug>/", frontend_views.article_detail, name="article_detail"),
    path("search/", frontend_views.static_search, name="search"),
    path("news/feed/", LatestArticlesFeed(), name="news_feed"),
    path(
        "<path:internal_path>/",
        frontend_views.managed_navigation_info,
        name="managed_navigation_info",
    ),
]


urlpatterns = core_urlpatterns + i18n_patterns(
    *frontend_urlpatterns,
    path("", include(wagtail_urls)),
    prefix_default_language=False,
)


if settings.DEBUG:
    import re

    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    from ai_author_forum.utils.media import serve_media

    urlpatterns += staticfiles_urlpatterns()
    media_prefix = settings.MEDIA_URL.lstrip("/").rstrip("/")
    urlpatterns += [
        re_path(
            rf"^{re.escape(media_prefix)}/(?P<path>.*)$",
            serve_media,
            {"document_root": settings.MEDIA_ROOT},
        )
    ]
