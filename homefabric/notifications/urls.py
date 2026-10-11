from django.urls import path

from homefabric.notifications import views

app_name = "notifications"
urlpatterns = [
    path("notifications/", views.channel_list, name="channels"),
    path("notifications/new/", views.channel_create, name="channel_create"),
    path("notifications/<uuid:pk>/edit/", views.channel_edit, name="channel_edit"),
    path("notifications/<uuid:pk>/test/", views.channel_test, name="channel_test"),
    path("notifications/<uuid:pk>/toggle/", views.channel_toggle, name="channel_toggle"),
    path("notifications/<uuid:pk>/delete/", views.channel_delete, name="channel_delete"),
    path("notifications/deliveries/<uuid:pk>/acknowledge/", views.acknowledge,
         name="acknowledge"),
]
