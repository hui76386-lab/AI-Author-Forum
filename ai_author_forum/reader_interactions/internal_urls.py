from django.urls import path

from . import internal_views

urlpatterns = [
    path("metrics/", internal_views.metrics, name="reader_internal_metrics"),
    path(
        "article-capabilities/<uuid:article_public_id>/",
        internal_views.article_capability_projection,
        name="reader_internal_article_capability",
    ),
    path(
        "comment-snapshots/rebuild/",
        internal_views.comment_snapshot_rebuild,
        name="reader_internal_comment_snapshot_rebuild",
    ),
    path(
        "moderation-commands/",
        internal_views.moderation_command_create,
        name="reader_internal_moderation_command_create",
    ),
    path(
        "moderation-commands/<uuid:command_id>/",
        internal_views.moderation_command_status,
        name="reader_internal_moderation_command_status",
    ),
]
