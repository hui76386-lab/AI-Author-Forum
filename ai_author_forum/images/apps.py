from django.apps import AppConfig


class ImagesConfig(AppConfig):
    default_auto_field: str = "django.db.models.AutoField"
    name = "ai_author_forum.images"

    def ready(self):
        from . import signals  # noqa: F401
