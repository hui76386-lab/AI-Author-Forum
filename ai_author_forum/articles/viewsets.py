from django.core.exceptions import PermissionDenied

from ai_author_forum.site_settings.admin_views import PermissionedModuleViewSet
from ai_author_forum.utils.admin_i18n import admin_text

from .views import ArticleReviewDashboardView


class ArticlesViewSet(PermissionedModuleViewSet):
    name = "articles"
    menu_label = admin_text("articles.manage")
    menu_name = "articles"
    menu_icon = "doc-full"
    menu_order = 220
    permission = "site_settings.access_articles"
    title = admin_text("articles.manage")
    description = admin_text("articles.manage.description")
    owner = "C：articles 应用；A 提供内容入口、角色权限和跨模块边界。"
    integration_points = (
        "ArticlePage",
        "get_approved_articles()",
        "get_article_context(slug)",
    )


class ArticleReviewViewSet(PermissionedModuleViewSet):
    name = "article-review"
    menu_label = admin_text("articles.review")
    menu_name = "article-review"
    menu_icon = "doc-full-inverse"
    menu_order = 225
    permission = "site_settings.access_article_review"
    title = admin_text("articles.review")
    description = admin_text("articles.review.description")
    owner = "C：articles 应用；A 提供审核入口、角色权限和审计接口。"
    integration_points = ("get_approved_articles()", "get_article_context(slug)")

    def index_view(self, request):
        if not self.has_access(request):
            raise PermissionDenied
        return ArticleReviewDashboardView.as_view()(request)
