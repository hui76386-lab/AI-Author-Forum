from django.apps import AppConfig


class JournalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_author_forum.journals"
    label = "journals"
    verbose_name = "Journals"
