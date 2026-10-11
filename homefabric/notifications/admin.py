from django.contrib import admin

from homefabric.notifications.models import NotificationDelivery, NotificationEndpoint


@admin.register(NotificationEndpoint)
class EndpointAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "provider", "enabled", "needs_verification")
    exclude = ("secret_value",)
    readonly_fields = ("masked_target",)


@admin.register(NotificationDelivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("kind", "recipient", "channel", "state", "attempts", "due_at", "sent_at")
    list_filter = ("state", "kind", "channel")
    readonly_fields = [f.name for f in NotificationDelivery._meta.fields]
