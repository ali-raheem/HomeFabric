from django.contrib import admin

from homefabric.core.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor_label", "action", "object_label", "household")
    list_filter = ("action",)
    search_fields = ("object_label", "actor_label", "object_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
