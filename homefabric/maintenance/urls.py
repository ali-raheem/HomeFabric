from django.urls import path

from homefabric.maintenance import views

app_name = "maintenance"
urlpatterns = [
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/<uuid:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<uuid:pk>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<uuid:pk>/start/", views.task_start, name="task_start"),
    path("tasks/<uuid:pk>/complete/", views.task_complete, name="task_complete"),
    path("tasks/<uuid:pk>/skip/", views.task_skip, name="task_skip"),
    path("tasks/<uuid:pk>/cancel/", views.task_cancel, name="task_cancel"),
    path("tasks/<uuid:pk>/follow-up/", views.task_follow_up, name="task_follow_up"),
    path("reminders/<str:owner>/<uuid:pk>/add/", views.reminder_add, name="reminder_add"),
    path("reminders/<uuid:pk>/delete/", views.reminder_delete, name="reminder_delete"),
    path("schedules/", views.schedule_list, name="schedule_list"),
    path("schedules/new/", views.schedule_create, name="schedule_create"),
    path("schedules/preview/", views.schedule_preview, name="schedule_preview"),
    path("schedules/<uuid:pk>/", views.schedule_detail, name="schedule_detail"),
    path("schedules/<uuid:pk>/edit/", views.schedule_edit, name="schedule_edit"),
    path("schedules/<uuid:pk>/toggle/", views.schedule_toggle, name="schedule_toggle"),
    path("schedules/<uuid:pk>/bulk-skip/", views.schedule_bulk_skip, name="schedule_bulk_skip"),
    path("items/<uuid:entity_pk>/history/new/", views.activity_create, name="activity_create"),
    path("history/<uuid:pk>/", views.activity_detail, name="activity_detail"),
    path("history/<uuid:pk>/correct/", views.activity_correct, name="activity_correct"),
]
