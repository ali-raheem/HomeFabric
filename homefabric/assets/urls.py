from django.urls import path

from homefabric.assets import views

app_name = "assets"
urlpatterns = [
    path("items/", views.entity_list, name="entity_list"),
    path("items/new/", views.entity_create, name="entity_create"),
    path("items/<uuid:pk>/", views.entity_detail, name="entity_detail"),
    path("items/<uuid:pk>/edit/", views.entity_edit, name="entity_edit"),
    path("items/<uuid:pk>/move/", views.entity_move, name="entity_move"),
    path("items/<uuid:pk>/archive/", views.entity_archive, name="entity_archive"),
    path("items/<uuid:pk>/restore/", views.entity_restore, name="entity_restore"),
    path("items/<uuid:pk>/relationships/add/", views.relationship_add, name="relationship_add"),
    path("relationships/<uuid:pk>/remove/", views.relationship_remove, name="relationship_remove"),
    path("explore/", views.explorer, name="explorer"),
    path("explore/<uuid:pk>/children/", views.explorer_children, name="explorer_children"),
    path("types/", views.type_list, name="type_list"),
]
