from django.urls import path

from homefabric.documents import views

app_name = "documents"
urlpatterns = [
    path("documents/", views.document_list, name="list"),
    path("documents/upload/", views.document_upload, name="upload"),
    path("documents/<uuid:pk>/", views.document_detail, name="detail"),
    path("documents/<uuid:pk>/edit/", views.document_edit, name="edit"),
    path("documents/<uuid:pk>/revise/", views.document_revise, name="revise"),
    path("documents/revisions/<uuid:pk>/download/", views.revision_download, name="download"),
    path("items/<uuid:entity_pk>/documents/attach/", views.attach_existing, name="attach"),
    path("documents/links/<int:pk>/detach/", views.detach, name="detach"),
]
