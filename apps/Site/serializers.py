from django.urls import reverse
from rest_framework import serializers

from apps.accounts.models.models import CustomUser, Profile
from apps.accounts.serializers.serializers import UserSerializer
from apps.media_files.models import ImageFile, VideoFile, AudioFile
from apps.media_files.serializers.serializers import (
    FileSerializer,
    ImageFileSerializer,
    VideoFileSerializer,
    AudioFileSerializer,
)

from .models import *

# class UserProfileSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Profile
#         fields = ["primary_avatar"]


class UserMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ["id", "username"]


class MessageSerializer(serializers.ModelSerializer):
    user = UserMessageSerializer(read_only=True)
    files = serializers.SerializerMethodField()
    # reactions_summary = serializers.SerializerMethodField()
    # user_reaction = serializers.SerializerMethodField()
    is_own = serializers.SerializerMethodField()
    # reply_to_message = serializers.SerializerMethodField()
    # view_count = serializers.SerializerMethodField()
    is_viewed = serializers.SerializerMethodField()
    viewers = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "value",
            "date",
            "user",
            # "chat",
            "files",
            # "reactions_summary",
            # "user_reaction",
            "is_own",
            # "is_deleted",
            # "edit_date",
            # "forwarded",
            # "reply_to",
            # "reply_to_message",
            "is_viewed",
            "viewers",
        ]
        read_only_fields = fields

    def get_files(self, obj):
        request = self.context.get("request")
        serialized_files = []
        for f in obj.file.all():
            if isinstance(f, ImageFile):
                serializer = ImageFileSerializer(f, context={"request": request})
            elif isinstance(f, VideoFile):
                serializer = VideoFileSerializer(f, context={"request": request})
            elif isinstance(f, AudioFile):
                serializer = AudioFileSerializer(f, context={"request": request})
            else:
                serializer = FileSerializer(f, context={"request": request})

            serialized_files.append(serializer.data)
        return serialized_files

    def get_is_viewed(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        recipients = getattr(obj, "read_recipients", [])

        return any(r.user_id == request.user.id for r in recipients)

    def get_reactions_summary(self, obj):
        from collections import Counter

        reaction_counts = Counter(
            reaction.reaction_type for reaction in obj.message_reactions.all()
        )

        return [
            {
                "type": reaction_type,
                "emoji": dict(MessageReaction.REACTION_TYPES).get(reaction_type, "❓"),
                "count": count,
            }
            for reaction_type, count in reaction_counts.items()
        ]

    def get_user_reaction(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            try:
                return obj.message_reactions.get(user=request.user).reaction_type
            except MessageReaction.DoesNotExist:
                return None
        return None

    def get_is_own(self, obj):
            request = self.context.get("request")
            if request and hasattr(request, "user"):
                return obj.user.id == request.user.id
    
            user_id = self.context.get("user_id")
            if user_id:
                return obj.user.id == user_id
    
            return False


    def get_reply_to_message(self, obj):
        if obj.reply_to and not obj.reply_to.is_deleted:
            return {
                "id": obj.reply_to.id,
                "value": obj.reply_to.value,
                "user": {
                    "id": obj.reply_to.user.id,
                    "username": obj.reply_to.user.username,
                },
                "date": obj.reply_to.date.isoformat() if obj.reply_to.date else None,
                "is_deleted": obj.reply_to.is_deleted,
            }
        return None

    def get_viewers(self, obj):
        recipients = getattr(obj, "read_recipients", [])

        recipients = [r for r in recipients if r.user_id != obj.user_id]

        return MessageRecipientSerializer(
            recipients, many=True, context=self.context
        ).data


class MessageRecipientSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = MessageRecipient
        fields = ["user", "read_date"]

    def get_user(self, obj):
        request = self.context.get("request")
        return UserViewerSerializer(obj.user, context={"request": request}).data


class UserViewerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ["id", "username"]


class MessageReactionSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = MessageReaction
        fields = ["id", "user", "reaction_type", "created_at"]
        read_only_fields = fields


class ChatSerializerContacts(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)

    class Meta:
        model = Chat
        fields = ("users",)
        read_only_fields = fields


class ContactSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    primary_media = serializers.SerializerMethodField()
    chat_id = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id",
            "user",
            "name",
            "avatar",
            "primary_media",
            "is_blocked",
            "is_favorite",
            "created_at",
            "chat_id",
        ]

    def _get_current_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def _get_display_cached(self, obj):
        if not hasattr(self, "_display_cache"):
            self._display_cache = {}

        if obj.pk not in self._display_cache:
            self._display_cache[obj.pk] = self._resolve_display(obj)
        return self._display_cache[obj.pk]

    def _resolve_display(self, obj):
        user = obj.user
        profile = getattr(user, "profile", None)

        name = obj.name.strip() if obj.name else user.display_name or user.username

        avatar = (
            obj.display_media.filter(is_primary=True).first()
            or (profile and profile.profile_media.filter(is_primary=True).first())
            or None
        )

        return {"name": name, "avatar": avatar}

    def get_name(self, obj):
        return self._get_display_cached(obj)["name"]

    def get_avatar(self, obj):
        avatar = self._get_display_cached(obj)["avatar"]
        if not avatar:
            return None

        concrete = avatar.concrete()
        if hasattr(concrete, "image_thumbnail_small"):
            return concrete.image_thumbnail_small.url
        elif hasattr(concrete, "image"):
            return concrete.image.url
        elif hasattr(concrete, "video"):
            return concrete.video.url
        return None

    def get_primary_media(self, obj):
        avatar = self._get_display_cached(obj)["avatar"]
        return (
            DisplayMediaSerializer(avatar, context=self.context).data
            if avatar
            else None
        )

    def get_chat_id(self, obj):
        user = self._get_current_user()
        chat = (
            Chat.objects.filter(chat_type=Chat.ChatType.DIALOG)
            .filter(users=user)
            .filter(users=obj.user)
            .distinct()
            .first()
        )
        return chat.id if chat else None


from rest_framework import serializers

from apps.accounts.serializers.serializers import UserSerializer
from apps.media_files.models.models import DisplayMedia
from apps.media_files.serializers.serializers import (
    DisplayMediaChatListSerializer,
    DisplayMediaSerializer,
)


class MessageChatListSerializer(MessageSerializer):
    user = UserMessageSerializer(read_only=True)
    files = serializers.SerializerMethodField()

    def get_files(self, obj):
        files = obj.file.all()[:3]
        serialized = []
        for f in files:
            if isinstance(f, ImageFile):
                serialized.append(ImageFileSerializer(f, context=self.context).data)
            elif isinstance(f, VideoFile):
                serialized.append(VideoFileSerializer(f, context=self.context).data)
            else:
                serialized.append(FileSerializer(f, context=self.context).data)
        return serialized

    class Meta(MessageSerializer.Meta):
        fields = ("id", "value", "files", "date", "user")


class ChatListSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    primary_media = serializers.SerializerMethodField()
    info = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            "id",
            "name",
            "chat_type",
            "last_message",
            "unread_count",
            "primary_media",
            "info",
        ]

    def _get_display_cached(self, obj):
        if not hasattr(self, "_display_cache"):
            self._display_cache = {}

        if obj.pk not in self._display_cache:
            self._display_cache[obj.pk] = self._resolve_display(obj)

        return self._display_cache[obj.pk]

    def _get_interlocutor_cached(self, obj):
        if not hasattr(self, "_interlocutor_cache"):
            self._interlocutor_cache = {}

        if obj.pk not in self._interlocutor_cache:
            interlocutors_map = self.context.get("interlocutors_map", {})
            self._interlocutor_cache[obj.pk] = interlocutors_map.get(obj.pk)

        return self._interlocutor_cache[obj.pk]

    def _get_contact_cached(self, interlocutor):
        if not hasattr(self, "_contact_cache"):
            self._contact_cache = {}
        if not interlocutor:
            return None

        key = interlocutor.pk
        if key not in self._contact_cache:
            self._contact_cache[key] = self.context["contacts_map"].get(key)
        return self._contact_cache[key]

    def _get_current_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def _get_first_media(self, obj, media_map=None):
        avatar_list = getattr(obj, "primary_display_media", [])
        if avatar_list:
            return avatar_list[0]

        if media_map:
            ct_chat_id = self.context.get("ct_chat_id")
            return media_map.get((ct_chat_id, obj.id))

        return None

    def _resolve_display(self, obj):
        user = self._get_current_user()
        media_map = self.context.get("media_map", {})

        if not obj.is_dialog or not user:
            avatar = self._get_first_media(obj, media_map)
            return {"name": obj.name, "avatar": avatar, "last_seen": None}

        interlocutor = self._get_interlocutor_cached(obj)
        if not interlocutor:
            return {"name": obj.name, "avatar": None, "last_seen": None}

        contact = self._get_contact_cached(interlocutor)
        profile = getattr(interlocutor, "profile", None)

        name = (
            contact.name
            if contact and contact.name
            else getattr(interlocutor, "display_name", "Unknown")
        )

        ct_contact_id = self.context.get("ct_contact_id")
        ct_profile_id = self.context.get("ct_profile_id")

        avatar = None
        if contact and ct_contact_id:
            avatar = media_map.get((ct_contact_id, contact.id))

        if not avatar and profile and ct_profile_id:
            avatar = media_map.get((ct_profile_id, profile.id))

        last_seen = getattr(profile, "last_seen", None)

        return {"name": name, "avatar": avatar, "last_seen": last_seen}

    def get_primary_media(self, obj):
        avatar = self._get_display_cached(obj)["avatar"]
        if isinstance(avatar, DisplayMedia):
            return DisplayMediaChatListSerializer(avatar, context=self.context).data
        return None

    def get_name(self, obj):
        return self._get_display_cached(obj)["name"]

    def get_info(self, obj):
        if obj.is_dialog:
            return self._get_display_cached(obj)["last_seen"]
        return obj.users_count

    def get_last_message(self, obj):
        message = getattr(obj, "last_message", None)
        return (
            MessageChatListSerializer(message, context=self.context).data
            if message
            else None
        )

    def get_unread_count(self, obj):
        return getattr(obj, "unread_count", 0)


class ChatUserSerializer(serializers.ModelSerializer):
    is_contact = serializers.SerializerMethodField()
    contact_id = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = (
            "id",
            "is_contact",
            "contact_id",
        )

    def _get_contacts_cache(self):
        if not hasattr(self, "_contacts_cache"):
            user = self.context["request"].user
            self._contacts_cache = {
                c.user_id: c for c in Contact.objects.filter(owner=user)
            }
        return self._contacts_cache

    def get_is_contact(self, obj):
        return self.get_contact_id(obj) is not None

    def get_contact_id(self, obj):
        contact = self._get_contacts_cache().get(obj.pk)
        return contact.id if contact else None


class ChatSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    media = serializers.SerializerMethodField()
    users = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            "messages",
            "media",
            "users",
        ]

    def get_users(self, obj):
        user = self._get_current_user()
        qs = obj.users.all()

        if user:
            qs = qs.exclude(pk=user.pk)

        return ChatUserSerializer(qs, many=True, context=self.context).data

    def _get_display_cached(self, obj):
        if not hasattr(self, "_display_cache"):
            self._display_cache = {}

        if obj.pk not in self._display_cache:
            self._display_cache[obj.pk] = self._resolve_display(obj)

        return self._display_cache[obj.pk]

    def _get_interlocutor_cached(self, obj):
        if not hasattr(self, "_interlocutor_cache"):
            self._interlocutor_cache = {}

        if obj.pk not in self._interlocutor_cache:
            user = self._get_current_user()
            self._interlocutor_cache[obj.pk] = (
                obj.get_interlocutor(user) if user else None
            )

        return self._interlocutor_cache[obj.pk]

    def _get_contact_cached(self, interlocutor):
        if not hasattr(self, "_contact_cache"):
            self._contact_cache = {}

        key = interlocutor.pk if interlocutor else None
        if key not in self._contact_cache:
            user = self._get_current_user()
            self._contact_cache[key] = (
                Contact.objects.filter(owner=user, user=interlocutor).first()
                if user and interlocutor
                else None
            )

        return self._contact_cache[key]

    def _get_current_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def _resolve_display(self, obj):
        user = self._get_current_user()

        if not obj.is_dialog or not user:
            avatar = obj.display_media.filter(is_primary=True).first()
            return {
                "name": obj.name,
                "avatar": avatar,
                "last_seen": None,
            }

        interlocutor = self._get_interlocutor_cached(obj)
        if not interlocutor:
            return {"name": obj.name, "avatar": None, "last_seen": None}

        contact = self._get_contact_cached(interlocutor)
        profile = getattr(interlocutor, "profile", None)

        if contact and contact.name:
            name = contact.name
        else:
            name = interlocutor.display_name

        avatar = None
        if contact:
            avatar = contact.display_media.filter(is_primary=True).first()
        if not avatar and profile:
            avatar = profile.profile_media.filter(is_primary=True).first()

        return {
            "name": name,
            "avatar": avatar,
            "last_seen": profile.last_seen if profile else None,
        }

    def get_media(self, obj):
        user = self._get_current_user()

        if obj.is_dialog and user:
            interlocutor = self._get_interlocutor_cached(obj)

            if not interlocutor:
                return []

            media_items = []

            contact = self._get_contact_cached(interlocutor)
            profile = getattr(interlocutor, "profile", None)

            display = self._get_display_cached(obj)
            display_avatar = display["avatar"]

            if contact:
                media_items.extend(contact.display_media.filter(is_primary=False))

            if profile:
                profile_primary = profile.profile_media.filter(is_primary=True).first()

                if profile_primary and profile_primary != display_avatar:
                    media_items.append(profile_primary)

                media_items.extend(profile.profile_media.filter(is_primary=False))

        else:
            media_items = list(obj.display_media.filter(is_primary=False))

        media_items = list(dict.fromkeys(media_items))
        return DisplayMediaSerializer(media_items, many=True, context=self.context).data


from django.conf import settings
from urllib.parse import urljoin


def build_absolute_url(path: str) -> str:
    scheme = settings.SITE_SCHEME
    domain = settings.SITE_DOMAIN
    base = f"{scheme}://{domain}"
    return urljoin(base, path)


class WallpaperSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = Wallpaper
        fields = ["id", "file", "url", "created_at", "type"]

    def get_url(self, obj):
        file = getattr(obj, "file", None)
        if not file or not getattr(file, "name", None):
            return None

        try:
            url = file.url
        except Exception:
            return None

        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(url)

        return build_absolute_url(url)
