from django.urls import path

from homefabric.manual import views

app_name = "manual"
urlpatterns = [
    path("household/manual/", views.options, name="options"),
    path("household/manual/view/", views.view, name="view"),
    path("household/manual/download/", views.download, name="download"),
]
