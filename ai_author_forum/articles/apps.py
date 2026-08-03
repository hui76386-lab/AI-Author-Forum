from django.apps import AppConfig


class ArticlesConfig(AppConfig):
    default_auto_field: str = "django.db.models.AutoField"
    name = "ai_author_forum.articles"
    label = "articles"

    def ready(self):
        from . import wagtail_hooks  # noqa: F401
