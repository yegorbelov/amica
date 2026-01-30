from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    # path(
    #     "robots.txt",
    #     TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
    # ),
    path("api/", include("apps.Site.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/media_files/", include("apps.media_files.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += [path("silk/", include("silk.urls", namespace="silk"))]
