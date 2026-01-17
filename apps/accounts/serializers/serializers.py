from rest_framework import serializers

from apps.media_files.serializers.serializers import DisplayMediaSerializer

from ..models import *


class ProfileSerializer(serializers.ModelSerializer):
    primary_avatar = serializers.SerializerMethodField()
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
            "primary_avatar",
            "media",
        )

    def get_primary_avatar(self, obj):
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

    class Meta:
        model = CustomUser
        fields = (
            "id",
            "email",
            "username",
            "profile",
            "preferred_session_lifetime_days",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        return ret


import re

from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken


class ActiveSessionSerializer(serializers.ModelSerializer):
    device = serializers.SerializerMethodField()
    is_current = serializers.SerializerMethodField()

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
        )

    def get_is_current(self, obj) -> bool:
        request = self.context.get("request")
        if not request:
            return False

        refresh = request.COOKIES.get("refresh_token")
        if not refresh:
            return False

        try:
            return RefreshToken(refresh)["jti"] == obj.jti
        except Exception:
            return False

    def get_device(self, obj):
        ua = obj.user_agent or ""

        browser = "Other"
        browser_version = ""
        browser_patterns = [
            ("Chrome", r"Chrome/([\d\.]+)"),
            ("Firefox", r"Firefox/([\d\.]+)"),
            ("Safari", r"Version/([\d\.]+).*Safari"),
            ("Edge", r"Edg/([\d\.]+)"),
            ("Opera", r"OPR/([\d\.]+)"),
        ]

        for name, pattern in browser_patterns:
            match = re.search(pattern, ua)
            if match:
                browser = name
                browser_version = match.group(1)
                break

        if browser_version:
            parts = browser_version.split(".")
            while len(parts) > 1 and parts[-1] == "0":
                parts.pop()
            browser_version = ".".join(parts)

        os = "Other"
        os_version = ""
        os_patterns = [
            ("Windows", r"Windows NT ([\d\.]+)"),
            ("Mac", r"Mac OS X ([\d_]+)"),
            ("Linux", r"Linux"),
            ("iOS", r"iPhone OS ([\d_]+)"),
            ("Android", r"Android ([\d\.]+)"),
        ]

        for name, pattern in os_patterns:
            match = re.search(pattern, ua)
            if match:
                os = name
                if match.groups():
                    os_version = match.group(1).replace("_", ".")
                break

        os_str = f"{os} {os_version}" if os_version else os
        browser_str = f"{browser} {browser_version}" if browser_version else browser

        return f"{browser_str} on {os_str}"
