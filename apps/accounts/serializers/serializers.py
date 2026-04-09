from django.contrib.gis.geoip2 import GeoIP2
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.media_files.serializers.serializers import DisplayMediaSerializer

from ..models import *
from ..session_payload import active_session_model_to_dict


class ProfileSerializer(serializers.ModelSerializer):
    primary_media = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = (
            "id",
            "last_seen",
            "bio",
            "phone",
            "date_of_birth",
            "location",
            "primary_media",
            "media",
        )

    def get_primary_media(self, obj):
        primary = obj.profile_media.filter(is_primary=True).first()
        if not primary:
            primary = obj.profile_media.order_by("-created_at").first()

        if not primary:
            return None

        return DisplayMediaSerializer(primary, context=self.context).data

    def get_media(self, obj):
        media_qs = obj.profile_media.filter(is_primary=False).order_by("-created_at")
        return DisplayMediaSerializer(media_qs, many=True, context=self.context).data


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    dm_chat_id = serializers.SerializerMethodField()
    has_trusted_device = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = (
            "id",
            "email",
            "username",
            "profile",
            "preferred_session_lifetime_days",
            "dm_chat_id",
            "has_trusted_device",
        )
        read_only_fields = fields

    def get_dm_chat_id(self, obj):
        mapping = self.context.get("dm_chat_by_peer_id") or {}
        return mapping.get(obj.id)

    def get_has_trusted_device(self, obj):
        return bool((obj.trusted_binding_hash or "").strip())

    # def to_representation(self, instance):
    #     ret = super().to_representation(instance)
    #     return ret


class ActiveSessionSerializer(serializers.ModelSerializer):
    """Uses one GeoIP2 reader + per-request IP cache (was 2× GeoIP2 per session)."""

    class Meta:
        model = ActiveSession
        fields = (
            "jti",
            "ip_address",
            "user_agent",
            "created_at",
            "expires_at",
            "last_active",
            "is_current",
            "device",
            "city",
            "country",
        )

    def to_representation(self, instance):
        request = self.context.get("request")
        current_jti = None
        if request:
            refresh = request.COOKIES.get("refresh_token")
            if refresh:
                try:
                    current_jti = str(RefreshToken(refresh)["jti"])
                except Exception:
                    pass

        bucket = self.context.setdefault(
            "_active_session_geo",
            {"geo": GeoIP2(), "ips": {}},
        )
        trusted_bh = None
        if request and request.user.is_authenticated:
            raw = getattr(request.user, "trusted_binding_hash", None) or ""
            trusted_bh = raw.strip() or None
        return active_session_model_to_dict(
            instance,
            current_jti=current_jti,
            geo=bucket["geo"],
            ip_cache=bucket["ips"],
            trusted_binding_hash=trusted_bh,
        )
