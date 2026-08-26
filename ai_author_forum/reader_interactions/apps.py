from django.apps import AppConfig


class ReaderInteractionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_author_forum.reader_interactions"
    verbose_name = "读者互动数据面"

    def ready(self):
        from .observability import install_celery_observability

        install_celery_observability()
