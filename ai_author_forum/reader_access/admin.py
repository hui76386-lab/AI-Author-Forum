from django.contrib import admin

from .models import (
    ControlPlaneOutbox,
    ModerationCommand,
    ProtectedArtifact,
    ProtectedManifest,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(ProtectedArtifact, ReadOnlyAdmin)
admin.site.register(ProtectedManifest, ReadOnlyAdmin)
admin.site.register(ControlPlaneOutbox, ReadOnlyAdmin)
admin.site.register(ModerationCommand, ReadOnlyAdmin)
