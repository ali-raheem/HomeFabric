from django.urls import path

from homefabric.operations import views

app_name = "operations"
urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
    path("instance/", views.instance_status, name="instance_status"),
    path("instance/integrity/", views.integrity, name="integrity"),
    path("instance/notifications/pause/", views.notifications_pause, name="notifications_pause"),
    path("instance/notifications/resume/", views.notifications_resume,
         name="notifications_resume"),
    path("household/export/", views.household_export, name="household_export"),
    path("household/audit/", views.household_audit, name="household_audit"),
]
