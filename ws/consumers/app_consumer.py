import asyncio
import logging

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.accounts.services.sessions import update_user_session_lifetime
from apps.Site.models import (
    Chat,
    Message,
    MessageReaction,
    MessageRecipient,
    UserWallpaper,
    Wallpaper,
)
from apps.Site.serializers import MessageSerializer
from channels.layers import get_channel_layer

from .base_consumer import BaseConsumer

logger = logging.getLogger(__name__)

MAX_CONCURRENT_BROADCASTS = 50


class AppConsumer(BaseConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chat_users_cache = {}

    async def handle_message(self, data):
        message_type = data.get("type")
        chat_id = data.get("chat_id")

        if (
            message_type in ("chat_message", "message_reaction", "message_viewed")
            and not chat_id
        ):
            return await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )

        try:
            if message_type == "chat_message":
                await self.handle_chat_message(data, chat_id)
            elif message_type == "message_reaction":
                await self.handle_message_reaction(data)
            elif message_type == "message_viewed":
                await self.handle_message_viewed(data)
            elif message_type == "set_session_lifetime":
                await self.set_session_lifetime(data)
            elif message_type == "add_user_wallpaper":
                await self.handle_add_user_wallpaper(data)
            elif message_type == "set_active_wallpaper":
                await self.handle_set_active_wallpaper(data)
            elif message_type == "delete_user_wallpaper":
                await self.handle_delete_user_wallpaper(data)
            else:
                logger.warning(f"Unknown message type: {message_type}")
        except Exception as e:
            logger.error(f"Error handling message type '{message_type}': {e}")

    async def handle_chat_message(self, data, chat_id):
        print(data)
        message_content = data.get("data", {}).get("value", "").strip()
        other_user_id = data.get("data", {}).get("user_id")

        if not message_content:
            return await self.send_json(
                {"type": "error", "message": "Message cannot be empty"}
            )

        if chat_id <= 0:
            print(other_user_id)
            if not other_user_id:
                return await self.send_json(
                    {"type": "error", "message": "user_id required"}
                )

            chat, created = await self.get_or_create_dialog(other_user_id)
            print(chat, created)

            await self.broadcast_to_chat_users(
                chat.id,
                "chat_created",
                {
                    "temp_chat_id": chat_id,
                    "chat": await self.serialize_chat(chat),
                },
            )

            chat_id = chat.id

        if not await self.user_in_chat(chat_id):
            return await self.send_json(
                {"type": "error", "message": "Not a member of chat"}
            )

        message = await self.save_message(chat_id, self.user, message_content)
        serialized = await self.serialize_message(message, self.user)

        await self.broadcast_to_chat_users(
            chat_id,
            "chat_message",
            {"chat_id": chat_id, "data": serialized},
        )

    @database_sync_to_async
    def get_or_create_dialog(self, other_user_id):
        from django.contrib.auth import get_user_model

        User = get_user_model()

        other_user = User.objects.get(id=other_user_id)
        return Chat.get_or_create_direct_chat(self.user, other_user)

    async def handle_message_reaction(self, data):
        message_id = data.get("message_id")
        if not message_id:
            return

        reaction_type = data.get("data", {}).get("reaction_type")

        chat_id = await self.get_message_chat_id(message_id)
        if not chat_id or not await self.user_in_chat(chat_id):
            return

        updated_message = await self.update_message_reaction(
            message_id, self.user, reaction_type
        )
        if not updated_message:
            return

        serialized = await self.serialize_message(updated_message, self.user)
        await self.broadcast_to_chat_users(
            chat_id,
            "message_reaction",
            {"message_id": message_id, "data": serialized},
        )

    async def handle_message_viewed(self, data):
        message_id = data.get("message_id")
        if not message_id:
            return

        chat_id = await self.get_message_chat_id(message_id)
        if not chat_id or not await self.user_in_chat(chat_id):
            return

        await self.mark_message_as_viewed(message_id, self.user)
        payload = {
            "message_id": message_id,
            "user_id": self.user.id,
            "username": self.user.username,
        }
        await self.broadcast_to_chat_users(chat_id, "message_viewed", payload)

    async def set_session_lifetime(self, data):
        days = data.get("days")
        if not days:
            return await self.send_json(
                {"type": "error", "message": "No session_lifetime_days provided"}
            )

        token = self.scope.get("refresh_token")
        await sync_to_async(update_user_session_lifetime)(
            self.user, days, current_refresh_token=token
        )
        await self.send_json({"type": "session_lifetime_updated", "days": days})

    async def user_in_chat(self, chat_id):
        user_ids = await self.get_chat_user_ids(chat_id)
        return self.user.id in user_ids

    @database_sync_to_async
    def get_chat_user_ids(self, chat_id):
        if chat_id in self.chat_users_cache:
            return self.chat_users_cache[chat_id]
        chat = Chat.objects.get(id=chat_id)
        user_ids = list(chat.users.values_list("id", flat=True))
        self.chat_users_cache[chat_id] = user_ids
        return user_ids

    @database_sync_to_async
    def get_message_chat_id(self, message_id):
        try:
            message = Message.objects.get(id=message_id)
            return message.chat.id
        except Message.DoesNotExist:
            return None

    @database_sync_to_async
    def save_message(self, chat_id, user, message_content):
        try:
            chat = Chat.objects.get(id=chat_id)
            message = Message.objects.create(
                chat=chat, user=user, value=message_content
            )
            return (
                Message.objects.filter(id=message.id)
                .select_related("user", "user__profile", "reply_to")
                .prefetch_related("file", "message_reactions")
                .first()
            )
        except Exception as e:
            logger.error(f"Error saving message: {e}")
            return None

    @database_sync_to_async
    def update_message_reaction(self, message_id, user, reaction_type):
        try:
            message = Message.objects.filter(id=message_id).first()
            if not message:
                return None

            if reaction_type is None:
                MessageReaction.objects.filter(message=message, user=user).delete()
            else:
                MessageReaction.objects.update_or_create(
                    message=message,
                    user=user,
                    defaults={"reaction_type": reaction_type},
                )
            return Message.objects.filter(id=message_id).first()
        except Exception as e:
            logger.error(f"Error updating message reaction: {e}")
            return None

    @database_sync_to_async
    def mark_message_as_viewed(self, message_id, user):
        try:
            recipient, _ = MessageRecipient.objects.get_or_create(
                message_id=message_id, user=user
            )
            if not recipient.read_date:
                recipient.read_date = timezone.now()
                recipient.save()
            return True
        except Exception as e:
            logger.error(f"Error marking message as viewed: {e}")
            return False

    @database_sync_to_async
    def serialize_message(self, message, requesting_user):
        serializer = MessageSerializer(message, context={"user_id": requesting_user.id})
        return serializer.data

    @database_sync_to_async
    def serialize_chat(self, chat):
        serializer = ChatListSerializer(chat)
        return serializer.data

    async def broadcast_to_chat_users(self, chat_id, event_type, payload):
        user_ids = await self.get_chat_user_ids(chat_id)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROADCASTS)

        async def send_to_user(user_id):
            async with semaphore:
                await self.send_to_user_group(user_id, event_type, **payload)

        await asyncio.gather(*(send_to_user(uid) for uid in user_ids))

    async def chat_message(self, event):
        await self.send_json(event)

    async def message_reaction(self, event):
        await self.send_json(event)

    async def message_viewed(self, event):
        await self.send_json(event)

    async def session_created(self, event):
        await self.send_json(event)

    async def session_lifetime_updated(self, event):
        await self.send_json(event)

    async def file_uploaded(self, event):
        await self.send_json({"type": "file_uploaded", "data": event["data"]})

    async def user_wallpaper_added(self, event):
        await self.send_json(
            {
                "type": "user_wallpaper_added",
                "data": event["data"],
            }
        )

    async def chat_created(self, event):
        await self.send_json(
            {
                "type": "chat_created",
                "temp_chat_id": event.get("temp_chat_id"),
                "chat": event.get("chat"),
            }
        )

    async def handle_set_active_wallpaper(self, data):
        wallpaper_id = data.get("data", {}).get("id")
        if not wallpaper_id:
            return await self.send_json(
                {"type": "error", "message": "No wallpaper id provided"}
            )

        profile = await database_sync_to_async(lambda: self.user.profile)()

        if str(wallpaper_id).startswith("default-"):

            def save_default():
                profile.active_wallpaper = None
                profile.default_wallpaper_id = wallpaper_id
                profile.save(update_fields=["active_wallpaper", "default_wallpaper_id"])
                return {
                    "id": wallpaper_id,
                }

            serialized = await database_sync_to_async(save_default)()
        else:
            wallpaper = await self.get_user_wallpaper(wallpaper_id)
            if not wallpaper:
                return await self.send_json(
                    {"type": "error", "message": "Wallpaper not found for user"}
                )

            def save_user_wallpaper():
                profile.active_wallpaper = wallpaper
                profile.default_wallpaper_id = None
                profile.save(update_fields=["active_wallpaper", "default_wallpaper_id"])
                return wallpaper

            wallpaper = await database_sync_to_async(save_user_wallpaper)()
            serialized = await self.serialize_wallpaper(wallpaper)

        channel_layer = get_channel_layer()
        group_name = f"user_{self.user.id}"

        await channel_layer.group_send(
            group_name,
            {
                "type": "active_wallpaper_updated",
                "data": serialized,
            },
        )

    async def active_wallpaper_updated(self, event):
        await self.send_json(
            {"type": "active_wallpaper_updated", "data": event["data"]}
        )

    @database_sync_to_async
    def get_user_wallpaper(self, wallpaper_id):
        try:
            return Wallpaper.objects.get(id=wallpaper_id, userwallpaper__user=self.user)
        except Wallpaper.DoesNotExist:
            return None

    @database_sync_to_async
    def set_active_wallpaper_in_profile(self, wallpaper):
        profile, _ = self.user.profile, True
        profile.active_wallpaper = wallpaper
        profile.save(update_fields=["active_wallpaper"])
        return True

    async def handle_add_user_wallpaper(self, data):
        file_data = data.get("data", {}).get("file")
        if not file_data:
            return await self.send_json(
                {"type": "error", "message": "No file data provided"}
            )

        wallpaper = await self.create_wallpaper(file_data)
        await self.add_user_wallpaper(self.user.id, wallpaper.id)

        serialized = await self.serialize_wallpaper(wallpaper)

        await self.send_json({"type": "user_wallpaper_added", "data": serialized})

    @database_sync_to_async
    def create_wallpaper(self, file_data):
        wallpaper = Wallpaper.objects.create(file=file_data)
        return wallpaper

    @database_sync_to_async
    def add_user_wallpaper(self, user_id, wallpaper_id):
        user = self.scope["user"]
        wallpaper = Wallpaper.objects.get(id=wallpaper_id)
        UserWallpaper.objects.create(user=user, wallpaper=wallpaper)
        return True

    @database_sync_to_async
    def serialize_wallpaper(self, wallpaper):
        from apps.Site.serializers import WallpaperSerializer

        serializer = WallpaperSerializer(wallpaper)
        return serializer.data

    async def handle_delete_user_wallpaper(self, data):
        wallpaper_id = data.get("data", {}).get("id")
        if not wallpaper_id:
            return await self.send_json(
                {"type": "error", "message": "No wallpaper id provided"}
            )

        deleted = await self.delete_user_wallpaper(self.user, wallpaper_id)
        if not deleted:
            return await self.send_json(
                {"type": "error", "message": "Wallpaper not found or cannot be deleted"}
            )

        channel_layer = get_channel_layer()
        group_name = f"user_{self.user.id}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "user_wallpaper_deleted",
                "id": wallpaper_id,
            },
        )

        def clear_active_wallpaper():
            profile = self.user.profile
            if profile.active_wallpaper and profile.active_wallpaper.id == wallpaper_id:
                profile.active_wallpaper = None
                profile.save(update_fields=["active_wallpaper"])
                return True
            return False

        cleared = await database_sync_to_async(clear_active_wallpaper)()

        if cleared:
            channel_layer = get_channel_layer()
            group_name = f"user_{self.user.id}"
            await channel_layer.group_send(
                group_name,
                {
                    "type": "active_wallpaper_updated",
                    "data": None,
                },
            )

    async def user_wallpaper_deleted(self, event):
        await self.send_json({"type": "user_wallpaper_deleted", "id": event.get("id")})

    @database_sync_to_async
    def delete_user_wallpaper(self, user, wallpaper_id):
        try:
            uw = UserWallpaper.objects.filter(user=user, wallpaper_id=wallpaper_id)
            if not uw.exists():
                return False
            uw.delete()
            Wallpaper.objects.filter(id=wallpaper_id).delete()
            return True
        except Exception as e:
            logger.error(f"Error deleting wallpaper: {e}")
            return False
