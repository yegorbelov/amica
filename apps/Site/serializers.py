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


def _generic_primary_media(manager):
    """Use prefetched GenericRelation cache; avoid .filter() which can re-query."""
    return next((m for m in manager.all() if m.is_primary), None)


def _generic_non_primary_media(manager):
    return [m for m in manager.all() if not m.is_primary]


def _iter_message_files_attachment_order(message, file_id_order=None):
    """
    Files in the order they were attached (M2M through row id).
    message.file.all() follows File.Meta.ordering; with identical uploaded_at
    that order is undefined and can differ from attachment order (WS vs reload).

    file_id_order: optional list of file pks from a batch through query
    (context ``message_file_ids_by_message_id``). If None, one DB query per message.
    """
    if getattr(message, "deleted_at", None):
        return
    if file_id_order is not None:
        file_pks = file_id_order
    else:
        through = Message.file.through
        file_attname = through._meta.get_field("file").attname
        file_pks = (
            through.objects.filter(message_id=message.pk)
            .order_by("pk")
            .values_list(file_attname, flat=True)
        )
    by_pk = {f.pk: f for f in message.file.all()}
    for pk in file_pks:
        instance = by_pk.get(pk)
        if instance is not None:
            yield instance


# class UserProfileSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Profile
#         fields = ["primary_media"]


class UserMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ["id", "username"]


class MessageSerializer(serializers.ModelSerializer):
    user = UserMessageSerializer(read_only=True)
    files = serializers.SerializerMethodField()
    reactions_summary = serializers.SerializerMethodField()
    user_reactions = serializers.SerializerMethodField()
    user_reaction = serializers.SerializerMethodField()
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
            "reactions_summary",
            "user_reactions",
            "user_reaction",
            "is_own",
            "edit_date",
            # "forwarded",
            # "reply_to",
            # "reply_to_message",
            "is_viewed",
            "viewers",
        ]
        read_only_fields = fields

    def get_files(self, obj):
        if getattr(obj, "deleted_at", None):
            return []
        request = self.context.get("request")
        serialized_files = []
        order_map = self.context.get("message_file_ids_by_message_id")
        file_order = order_map.get(obj.pk, []) if order_map is not None else None
        for f in _iter_message_files_attachment_order(obj, file_id_order=file_order):
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
        return bool(getattr(obj, "read_recipients", []))

    def _get_current_user_id(self):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return request.user.id
        user_id = self.context.get("user_id")
        return user_id

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

    def get_user_reactions(self, obj):
        user_id = self._get_current_user_id()
        if not user_id:
            return []
        return [
            item.reaction_type
            for item in obj.message_reactions.all()
            if item.user_id == user_id
        ]

    def get_user_reaction(self, obj):
        reactions = self.get_user_reactions(obj)
        return reactions[0] if reactions else None

    def get_is_own(self, obj):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            return obj.user.id == request.user.id

        user_id = self.context.get("user_id")
        if user_id:
            return obj.user.id == user_id

        return False

    def get_reply_to_message(self, obj):
        if obj.reply_to and not obj.reply_to.deleted_at:
            return {
                "id": obj.reply_to.id,
                "value": obj.reply_to.value,
                "user": {
                    "id": obj.reply_to.user.id,
                    "username": obj.reply_to.user.username,
                },
                "date": obj.reply_to.date.isoformat() if obj.reply_to.date else None,
                "deleted_at": obj.reply_to.deleted_at,
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
    # user = UserSerializer(read_only=True)
    name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    primary_media = serializers.SerializerMethodField()
    chat_id = serializers.SerializerMethodField()
    last_seen = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id",
            "user_id",
            # "user",
            "name",
            "avatar",
            "primary_media",
            "is_blocked",
            "is_favorite",
            "created_at",
            "chat_id",
            "last_seen",
        ]

    def _get_current_user(self):
        user = self.context.get("user")
        if user is not None:
            return user
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
    
        contact_media = next(
            (m for m in obj.display_media.all() if m.is_primary),
            None
        )
    
        profile_media = None
        if profile:
            profile_media = next(
                (m for m in profile.profile_media.all() if m.is_primary),
                None
            )
    
        avatar = contact_media or profile_media

        last_seen = getattr(profile, "last_seen", None) if profile else None

        return {"name": name, "avatar": avatar, "last_seen": last_seen}

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
        dialog_map = self.context.get("dialog_map", {})
        return dialog_map.get(obj.user.id)

    def get_last_seen(self, obj):
        return self._get_display_cached(obj).get("last_seen")


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
        serialized = []
        for f in _iter_message_files_attachment_order(obj):
            if isinstance(f, ImageFile):
                serialized.append(ImageFileSerializer(f, context=self.context).data)
            elif isinstance(f, VideoFile):
                serialized.append(VideoFileSerializer(f, context=self.context).data)
            elif isinstance(f, AudioFile):
                serialized.append(AudioFileSerializer(f, context=self.context).data)
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
    type = serializers.CharField(source="chat_type")
    peer_user_id = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            "id",
            "name",
            "type",
            "last_message",
            "unread_count",
            "primary_media",
            "info",
            "peer_user_id",
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
        user = self.context.get("user")
        if user is not None:
            return user
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
            MessageSerializer(message, context=self.context).data
            if message
            else None
        )

    def get_unread_count(self, obj):
        return getattr(obj, "unread_count", 0)

    def get_peer_user_id(self, obj):
        if not obj.is_dialog:
            return None
        interlocutor = self._get_interlocutor_cached(obj)
        return interlocutor.id if interlocutor else None


from apps.accounts.serializers.serializers import ProfileSerializer
class ChatUserSerializer(serializers.ModelSerializer):
    is_contact = serializers.SerializerMethodField()
    contact_id = serializers.SerializerMethodField()
    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = CustomUser
        fields = (
            "id",
            "username",
            "email",
            "is_contact",
            "contact_id",
            "profile",
        )

    def _get_contacts_cache(self):
        if not hasattr(self, "_contacts_cache"):
            user = self.context.get("user") or getattr(
                self.context.get("request"), "user", None
            )
            self._contacts_cache = (
                {c.user_id: c for c in Contact.objects.filter(owner=user)}
                if user
                else {}
            )
        return self._contacts_cache

    def get_is_contact(self, obj):
        return self.get_contact_id(obj) is not None

    def get_contact_id(self, obj):
        contact = self._get_contacts_cache().get(obj.pk)
        return contact.id if contact else None


class ChatSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    media = serializers.SerializerMethodField()
    members = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            "messages",
            "media",
            "members",
        ]

    def get_members(self, obj):
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
            if not user or not obj.is_dialog:
                self._interlocutor_cache[obj.pk] = None
            else:
                others = [u for u in obj.users.all() if u.pk != user.pk]
                self._interlocutor_cache[obj.pk] = others[0] if others else None

        return self._interlocutor_cache[obj.pk]

    def _get_contact_cached(self, interlocutor):
        if not hasattr(self, "_contact_cache"):
            self._contact_cache = {}

        key = interlocutor.pk if interlocutor else None
        if key not in self._contact_cache:
            ctx_peer = self.context.get("dialog_contact_interlocutor_id")
            if interlocutor and ctx_peer == interlocutor.pk:
                self._contact_cache[key] = self.context.get("dialog_contact")
            else:
                user = self._get_current_user()
                self._contact_cache[key] = (
                    Contact.objects.filter(owner=user, user=interlocutor).first()
                    if user and interlocutor
                    else None
                )

        return self._contact_cache[key]

    def _get_current_user(self):
        user = self.context.get("user")
        if user is not None:
            return user
        request = self.context.get("request")
        return getattr(request, "user", None)

    def _resolve_display(self, obj):
        user = self._get_current_user()

        if not obj.is_dialog or not user:
            avatar = _generic_primary_media(obj.display_media)
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
            avatar = _generic_primary_media(contact.display_media)
        if not avatar and profile:
            avatar = _generic_primary_media(profile.profile_media)

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
                media_items.extend(_generic_non_primary_media(contact.display_media))

            if profile:
                profile_primary = _generic_primary_media(profile.profile_media)

                if profile_primary and profile_primary != display_avatar:
                    media_items.append(profile_primary)

                media_items.extend(_generic_non_primary_media(profile.profile_media))

        else:
            media_items = _generic_non_primary_media(obj.display_media)

        media_items = list(dict.fromkeys(media_items))
        return DisplayMediaSerializer(media_items, many=True, context=self.context).data


from django.conf import settings
from urllib.parse import urljoin


def build_absolute_url(path: str) -> str:
    scheme = getattr(settings, "SITE_SCHEME", "http")
    domain = getattr(settings, "SITE_DOMAIN", "localhost")
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
