from django.apps import AppConfig


class ImagesConfig(AppConfig):
    default_auto_field: str = "django.db.models.AutoField"
    name = "ai_author_forum.images"

    def ready(self):
        from . import signals  # noqa: F401
        from .permissions import install_journal_image_permission_policy

        install_journal_image_permission_policy()
