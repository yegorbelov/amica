from rest_framework import serializers

from ..models import *


class DisplayPhotoSerializer(serializers.ModelSerializer):
    small = serializers.SerializerMethodField()
    medium = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()

    class Meta:
        model = DisplayPhoto
        fields = [
            "id",
            "type",
            "small",
            "medium",
            "is_primary",
            "created_at",
        ]

    def _get_thumbnail_url(self, obj, version):
        print("VERSION:", obj, version)
        if getattr(obj, version, None):
            request = self.context.get("request")
            url = reverse(
                "protected-file-versioned", args=[obj.id, "display_photo", version]
            )
            if request:
                return request.build_absolute_uri(url)
            return url
        return None

    def get_small(self, obj):
        return self._get_thumbnail_url(obj, "thumbnail_small")

    def get_medium(self, obj):
        return self._get_thumbnail_url(obj, "thumbnail_medium")

    def get_type(self, obj):
        return "photo"

    def _build_url(self, field):
        if field is None or not getattr(field, "name", None):
            return None

        try:
            url = field.url
        except Exception:
            return None

        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class DisplayVideoSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()

    class Meta:
        model = DisplayVideo
        fields = [
            "id",
            "type",
            "url",
            "duration",
            "is_primary",
            "created_at",
            "updated_at",
        ]

    def get_url(self, obj):
        video = getattr(obj, "video", None)
        if not video or not getattr(video, "name", None):
            return None

        try:
            url = video.url
        except Exception:
            return None

        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_type(self, obj):
        return "video"


class DisplayMediaSerializer(serializers.Serializer):
    def to_representation(self, instance):
        instance = instance.concrete()

        if isinstance(instance, DisplayPhoto):
            return DisplayPhotoSerializer(instance, context=self.context).data

        if isinstance(instance, DisplayVideo):
            return DisplayVideoSerializer(instance, context=self.context).data

        return {}


class DisplayPhotoChatListSerializer(DisplayPhotoSerializer):
    class Meta(DisplayPhotoSerializer.Meta):
        fields = [
            "id",
            "type",
            "small",
            "medium",
        ]


class DisplayVideoChatListSerializer(DisplayVideoSerializer):
    class Meta(DisplayVideoSerializer.Meta):
        fields = [
            "id",
            "type",
            "url",
        ]


class DisplayMediaChatListSerializer(DisplayMediaSerializer):
    def to_representation(self, instance):
        instance = instance.concrete()

        if isinstance(instance, DisplayPhoto):
            return DisplayPhotoChatListSerializer(instance, context=self.context).data

        if isinstance(instance, DisplayVideo):
            return DisplayVideoChatListSerializer(instance, context=self.context).data

        return {}


class DisplayMediaCreateSerializer(serializers.Serializer):
    file = serializers.FileField()
    is_primary = serializers.BooleanField(default=False, write_only=True)

    def create(self, validated_data):
        obj = self.context["object"]
        is_primary = validated_data.pop("is_primary", True)
        file = validated_data.pop("file")

        ext = file.name.split(".")[-1].lower()
        if ext in ["jpg", "jpeg", "png", "webp", "gif"]:
            media = DisplayPhoto.objects.create(
                content_object=obj, image=file, is_primary=True, **validated_data
            )
        elif ext in ["mp4", "mov", "webm"]:
            media = DisplayVideo.objects.create(
                content_object=obj, video=file, **validated_data
            )
        else:
            raise serializers.ValidationError("Unsupported file type")

        if is_primary:
            media.is_primary = True
            media.save()

        return media


class FileSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = File
        fields = [
            "id",
            "file_url",
            "extension",
            "category",
            "original_name",
            "file_size",
            "uploaded_at",
        ]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file:
            if request:
                return request.build_absolute_uri(
                    reverse("protected-file-default", args=[obj.id])
                )
            else:
                return reverse("protected-file-default", args=[obj.id])
        return None


from django.urls import reverse


class ImageFileSerializer(FileSerializer):
    thumbnail_small_url = serializers.SerializerMethodField()
    thumbnail_medium_url = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    dominant_color = serializers.SerializerMethodField()

    class Meta(FileSerializer.Meta):
        fields = FileSerializer.Meta.fields + [
            "thumbnail_small_url",
            "thumbnail_medium_url",
            "width",
            "height",
            "dominant_color",
        ]

    def get_thumbnail_url(self, obj, version):
        if getattr(obj, version, None):
            request = self.context.get("request")
            url = reverse("protected-file-versioned", args=[obj.id, version])
            if request:
                return request.build_absolute_uri(url)
            return url
        return None

    def get_thumbnail_small_url(self, obj):
        return self.get_thumbnail_url(obj, "thumbnail_small")

    def get_thumbnail_medium_url(self, obj):
        return self.get_thumbnail_url(obj, "thumbnail_medium")

    def get_width(self, obj):
        return getattr(obj, "width", None)

    def get_height(self, obj):
        return getattr(obj, "height", None)

    def get_dominant_color(self, obj):
        return getattr(obj, "dominant_color", None)


class VideoFileSerializer(FileSerializer):
    width = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    # duration = serializers.SerializerMethodField()
    has_audio = serializers.SerializerMethodField()

    class Meta(FileSerializer.Meta):
        fields = FileSerializer.Meta.fields + ["width", "height", "has_audio"]

    def get_width(self, obj):
        return getattr(obj, "width", None)

    def get_height(self, obj):
        return getattr(obj, "height", None)

    def get_has_audio(self, obj):
        return getattr(obj, "has_audio", None)

    # def get_duration(self, obj):
    #     return getattr(obj, 'duration', None)


from django.urls import reverse


class AudioFileSerializer(FileSerializer):
    duration = serializers.SerializerMethodField()
    waveform = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()

    class Meta(FileSerializer.Meta):
        fields = FileSerializer.Meta.fields + ["duration", "waveform", "cover_url"]

    def get_duration(self, obj):
        return getattr(obj, "duration", None)

    def get_waveform(self, obj):
        return getattr(obj, "waveform", None)

    def get_cover_url(self, obj):
        if not obj.cover:
            return None

        url = reverse("protected-file-versioned", args=[obj.id, "cover"])
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(url)
        return url
