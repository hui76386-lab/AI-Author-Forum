from django.urls import path

from .views import (
    AccountActivateView,
    AccountCreateView,
    AccountDetailView,
    AccountListView,
    AccountResetPasswordView,
    AccountStatusView,
    LegacyRequiredPasswordChangeRedirectView,
)

app_name = "account_admin"

urlpatterns = [
    path("", AccountListView.as_view(), name="index"),
    path("new/", AccountCreateView.as_view(), name="new"),
    path(
        "change-password/",
        LegacyRequiredPasswordChangeRedirectView.as_view(),
        name="change_password",
    ),
    path("<int:account_id>/", AccountDetailView.as_view(), name="detail"),
    path(
        "<int:account_id>/suspend/",
        AccountStatusView.as_view(action="suspend"),
        name="suspend",
    ),
    path(
        "<int:account_id>/deactivate/",
        AccountStatusView.as_view(action="deactivate"),
        name="deactivate",
    ),
    path(
        "<int:account_id>/activate/",
        AccountActivateView.as_view(),
        name="activate",
    ),
    path(
        "<int:account_id>/reset-password/",
        AccountResetPasswordView.as_view(),
        name="reset_password",
    ),
]
