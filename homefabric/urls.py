from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "HomeFabric administration"
admin.site.site_title = "HomeFabric admin"

urlpatterns = [
    path("", include("homefabric.core.urls")),
    path("", include("homefabric.accounts.urls")),
    path("", include("homefabric.assets.urls")),
    path("", include("homefabric.documents.urls")),
    path("", include("homefabric.maintenance.urls")),
    path("", include("homefabric.notifications.urls")),
    path("", include("homefabric.operations.urls")),
    path("", include("homefabric.manual.urls")),
    path("django-admin/", admin.site.urls),
]

handler403 = "homefabric.core.errors_views.permission_denied"
handler404 = "homefabric.core.errors_views.not_found"
handler500 = "homefabric.core.errors_views.server_error"
