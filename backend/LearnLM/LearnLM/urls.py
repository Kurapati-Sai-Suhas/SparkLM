from django.contrib import admin
from django.urls import path,include
from django.conf import settings
from django.conf.urls.static import static

from common.health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include('groups.urls')),
    path("healthz", healthz, name="healthz"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


