from django.urls import path
from wagtail import hooks

from .admin_views import ProtectedImageDeleteBulkAction, ProtectedImageDeleteView


@hooks.register("register_admin_urls", order=-100)
def register_protected_image_delete_url():
    return [
        path(
            "images/<int:image_id>/delete/",
            ProtectedImageDeleteView.as_view(),
            name="protected_image_delete",
        )
    ]


hooks.register("register_bulk_action", ProtectedImageDeleteBulkAction, order=1000)
