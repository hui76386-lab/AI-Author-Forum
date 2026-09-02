from django.urls import path

from .views import RequiredPasswordChangeView

app_name = "account"

urlpatterns = [
    path(
        "change-password/", RequiredPasswordChangeView.as_view(), name="change_password"
    ),
]
