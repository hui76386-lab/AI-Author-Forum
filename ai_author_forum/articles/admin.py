from django.contrib import admin

from .models import ArticleReviewRecord


@admin.register(ArticleReviewRecord)
class ArticleReviewRecordAdmin(admin.ModelAdmin):
    list_display = ("article", "reviewer", "action", "created_at")
    list_filter = ("action", "created_at")
    readonly_fields = ("created_at",)
    search_fields = ("article__title", "reviewer__username", "comment")
