from django.contrib import admin

from homefabric.operations.models import SchedulerHeartbeat, SystemState

admin.site.register(SystemState)
admin.site.register(SchedulerHeartbeat)
