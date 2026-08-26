from django.contrib import admin

from .models import (
    ArticleCapabilityProjection,
    Comment,
    CommentModerationEvent,
    CommentReport,
    CommentSnapshot,
    DownloadGrant,
    EmailVerificationChallenge,
    IdempotencyRecord,
    InteractionOutbox,
    ReaderActionEvent,
    ReaderDeviceFlow,
    ReaderIdentity,
    ReaderSession,
)


class ReadOnlyInteractionAdmin(admin.ModelAdmin):
    using = "interactions"

    def get_queryset(self, request):
        return super().get_queryset(request).using(self.using)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (
    ReaderIdentity,
    EmailVerificationChallenge,
    ReaderDeviceFlow,
    ReaderSession,
    ArticleCapabilityProjection,
    Comment,
    CommentModerationEvent,
    CommentReport,
    DownloadGrant,
    ReaderActionEvent,
    InteractionOutbox,
    IdempotencyRecord,
    CommentSnapshot,
):
    admin.site.register(model, ReadOnlyInteractionAdmin)
