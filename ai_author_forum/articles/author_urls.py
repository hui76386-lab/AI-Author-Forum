from django.urls import path
from django.views.generic import RedirectView

from .author_views import (
    AuthorDashboardView,
    AuthorJournalListView,
    AuthorLoginView,
    AuthorLogoutView,
    AuthorSubmissionChangeJournalView,
    AuthorSubmissionCreateView,
    AuthorSubmissionDetailView,
    AuthorSubmissionEditView,
    AuthorSubmissionHistoryView,
    AuthorSubmissionSubmitView,
)

app_name = "author"

urlpatterns = [
    path("login/", AuthorLoginView.as_view(), name="login"),
    path("logout/", AuthorLogoutView.as_view(), name="logout"),
    path("", AuthorDashboardView.as_view(), name="dashboard"),
    path(
        "dashboard/",
        RedirectView.as_view(pattern_name="author:dashboard", query_string=True),
        name="dashboard_alias",
    ),
    path(
        "articles/",
        RedirectView.as_view(pattern_name="author:submissions", query_string=True),
        name="articles_alias",
    ),
    path("journals/", AuthorJournalListView.as_view(), name="journals"),
    path("submissions/", AuthorDashboardView.as_view(), name="submissions"),
    path("submissions/new/", AuthorSubmissionCreateView.as_view(), name="new"),
    path(
        "submissions/<int:article_id>/",
        AuthorSubmissionDetailView.as_view(),
        name="detail",
    ),
    path(
        "submissions/<int:article_id>/edit/",
        AuthorSubmissionEditView.as_view(),
        name="edit",
    ),
    path(
        "submissions/<int:article_id>/change-journal/",
        AuthorSubmissionChangeJournalView.as_view(),
        name="change_journal",
    ),
    path(
        "submissions/<int:article_id>/submit/",
        AuthorSubmissionSubmitView.as_view(),
        name="submit",
    ),
    path(
        "submissions/<int:article_id>/history/",
        AuthorSubmissionHistoryView.as_view(),
        name="history",
    ),
]
