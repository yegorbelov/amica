from rest_framework.routers import DefaultRouter

from apps.media_files.views import DisplayMediaViewSet

router = DefaultRouter()
router.register(r"primary-media", DisplayMediaViewSet, basename="primary-media")

urlpatterns = router.urls