"""Service to build general info for a user. Used by get_general_info API and WebSocket get_general_info."""

import logging

from django.db.models import Prefetch

from apps.accounts.models.models import CustomUser
from apps.accounts.serializers.serializers import UserSerializer
from apps.media_files.models.models import DisplayPhoto, DisplayVideo
from apps.Site.serializers import WallpaperSerializer

logger = logging.getLogger(__name__)


def get_general_info_for_user(user):
    """
    Return {"success": True, "user": ..., "active_wallpaper": ...} for the given user.
    Same data as get_general_info API; context without request for serializers.
    """
    try:
        user_obj = (
            CustomUser.objects.select_related("profile")
            .prefetch_related(
                Prefetch(
                    "profile__profile_media",
                    queryset=DisplayPhoto.objects.all(),
                    to_attr="prefetched_photos",
                ),
                Prefetch(
                    "profile__profile_media",
                    queryset=DisplayVideo.objects.all(),
                    to_attr="prefetched_videos",
                ),
            )
            .get(pk=user.pk)
        )
    except CustomUser.DoesNotExist:
        logger.warning("User not found", extra={"user_id": user.id})
        return {"success": False, "error": "User not found"}

    profile = getattr(user_obj, "profile", None)
    active_wallpaper = None

    if profile:
        if profile.active_wallpaper:
            active_wallpaper = WallpaperSerializer(
                profile.active_wallpaper,
                context={},
            ).data
        elif profile.default_wallpaper_id:
            active_wallpaper = {"id": profile.default_wallpaper_id}
        else:
            active_wallpaper = {"id": "default-0"}

    serializer = UserSerializer(user_obj, context={"request": None})
    return {
        "success": True,
        "user": serializer.data,
        "active_wallpaper": active_wallpaper,
    }
