from django.urls import path

from homefabric.core import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("search/", views.search, name="search"),
]
