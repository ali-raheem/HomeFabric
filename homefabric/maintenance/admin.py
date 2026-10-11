from django.contrib import admin

from homefabric.maintenance.models import ActivityEntry, MaintenanceSchedule, Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "entity", "status", "due_date", "assignee", "household")
    list_filter = ("status", "household")
    search_fields = ("title",)


@admin.register(MaintenanceSchedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("title", "entity", "mode", "interval_count", "interval_unit", "enabled")


@admin.register(ActivityEntry)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("occurred_on", "event_type", "entity", "author", "superseded")
    list_filter = ("event_type", "superseded")

    def has_change_permission(self, request, obj=None):
        return False  # History is corrected in the app, never edited in place.
