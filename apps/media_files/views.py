from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import DisplayPhoto, DisplayVideo
from .serializers.serializers import (
    DisplayMediaCreateSerializer,
    DisplayMediaSerializer,
)


class DisplayMediaViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_object_instance(self):
        content_type = self.request.query_params.get("content_type")
        object_id = self.request.query_params.get("object_id")
        if not content_type or not object_id:
            return None

        app_label_mapping = {
            "profile": "accounts",
        }
        app_label = app_label_mapping.get(content_type)

        if app_label:
            ct = ContentType.objects.get(model=content_type, app_label=app_label)
        else:
            ct = ContentType.objects.filter(model=content_type).first()
            if not ct:
                return None

        model_class = ct.model_class()
        return get_object_or_404(model_class, pk=object_id)

    def list(self, request, *args, **kwargs):
        obj = self.get_object_instance()
        if not obj:
            return Response({"detail": "Missing content_type or object_id"}, status=400)

        photos = DisplayPhoto.objects.filter(content_object=obj)
        videos = DisplayVideo.objects.filter(content_object=obj)
        media = list(photos) + list(videos)

        serializer = DisplayMediaSerializer(
            media, many=True, context={"request": request}
        )
        return Response(serializer.data)

    def notify_avatar_updated(self, profile, request=None):
        primary_media = profile.profile_media.filter(is_primary=True).first()
        if not primary_media:
            return

        serializer = DisplayMediaSerializer(primary_media, context={"request": request})
        data = serializer.data
        channel_layer = get_channel_layer()
        user_id = profile.user.id

        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "file_uploaded",
                "data": {
                    "object_id": user_id,
                    "content_type": "profile",
                    "media": data,
                },
            },
        )

    def create(self, request, *args, **kwargs):
        obj = self.get_object_instance()
        if not obj:
            return Response({"detail": "Missing content_type or object_id"}, status=400)

        serializer = DisplayMediaCreateSerializer(
            data=request.data, context={"object": obj}
        )
        serializer.is_valid(raise_exception=True)
        media = serializer.save()
        output_serializer = DisplayMediaSerializer(media, context={"request": request})

        # Profile has .user and .profile_media; Chat/Contact use other relations.
        if hasattr(obj, "profile_media") and getattr(obj, "user", None) == request.user:
            self.notify_avatar_updated(obj, request=request)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def set_primary(self, request, pk=None):
        media_photo = DisplayPhoto.objects.filter(pk=pk).first()
        media_video = DisplayVideo.objects.filter(pk=pk).first()
        media = media_photo or media_video
        if not media:
            return Response({"detail": "Media not found"}, status=404)

        media.is_primary = True
        media.save()
        serializer = DisplayMediaSerializer(media, context={"request": request})
        return Response(serializer.data)

    def destroy(self, request, pk=None):
        media_photo = DisplayPhoto.objects.filter(pk=pk).first()
        media_video = DisplayVideo.objects.filter(pk=pk).first()
        media = media_photo or media_video
        if not media:
            return Response({"detail": "Media not found"}, status=404)
        media.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
