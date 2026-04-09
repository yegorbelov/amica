import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import ActiveSession
from apps.accounts.serializers.serializers import UserSerializer
from apps.accounts.session_payload import serialize_active_sessions_for_ws_user
from apps.accounts.services.sessions import (
    revoke_active_session_for_user,
    revoke_other_active_sessions_for_user,
    update_user_session_lifetime,
)
from apps.accounts.backup_codes import verify_and_consume_backup_code
from apps.accounts.totp_service import user_totp_gate_ok
from apps.accounts.device_trust import (
    binding_matches_trusted,
    ensure_trusted_from_session_binding,
)
from apps.accounts.login_gate import deferred_login_payload
from apps.accounts.recovery_service import (
    create_email_verification_otp,
    send_email_verification_code_email,
)
from apps.accounts.session_binding import (
    binding_from_scope,
    ip_and_user_agent_from_scope,
    session_binding_matches_session,
    stable_device_login_challenge_binding_from_scope,
)
from apps.accounts.views import (
    create_refresh_token_for_user,
    get_access_token_for_session,
    remember_session_from_scope,
)
from apps.Site.models import (
    Chat,
    ChatMember,
    Contact,
    Message,
    MessageReaction,
    MessageRecipient,
    UserWallpaper,
    Wallpaper,
)
from apps.Site.serializers import ChatListSerializer, ChatUserSerializer, MessageSerializer
from apps.Site.services.get_chats_service import get_chats_list
from apps.Site.services.get_chat_service import get_chat_for_user
from apps.Site.services.get_contacts_service import get_contacts_for_user
from apps.Site.services.create_group_service import create_group_and_serialize
from apps.Site.services.get_general_info_service import get_general_info_for_user
from channels.layers import get_channel_layer

from .base_consumer import BaseConsumer

logger = logging.getLogger(__name__)

MAX_CONCURRENT_BROADCASTS = 50


def _add_contact_via_ws(owner_id, target_user_id):
    """Sync helper for WebSocket add_contact (runs in thread pool)."""
    User = get_user_model()
    try:
        owner = User.objects.get(pk=owner_id)
    except User.DoesNotExist:
        return None, "Not authenticated"
    if target_user_id == owner_id:
        return None, "You cannot add yourself to contacts"
    try:
        target = User.objects.get(pk=target_user_id)
    except User.DoesNotExist:
        return None, "User not found"
    from django.db import IntegrityError

    try:
        contact, _created = Contact.objects.get_or_create(owner=owner, user=target)
    except IntegrityError:
        return None, "Contact already exists"
    return (
        {
            "type": "contact_added",
            "user_id": target_user_id,
            "contact_id": contact.id,
            "name": contact.name or "",
        },
        None,
    )
WS_CHUNK_ACK_BATCH_SIZE = 6
WS_CHUNK_ACK_MAX_DELAY_MS = 90


class AppConsumer(BaseConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chat_users_cache = {}
        self.pending_chunk_parts = {}
        self.pending_chunk_ack_request_ids = []
        self.pending_chunk_ack_chunk_indexes = []
        self.pending_chunk_ack_flush_task = None

    async def _delayed_flush_chunk_acks(self):
        try:
            await asyncio.sleep(WS_CHUNK_ACK_MAX_DELAY_MS / 1000)
            await self._flush_chunk_acks()
        except asyncio.CancelledError:
            return

    async def _flush_chunk_acks(self):
        request_ids = self.pending_chunk_ack_request_ids
        chunk_indexes = self.pending_chunk_ack_chunk_indexes
        if not request_ids:
            self.pending_chunk_ack_request_ids = []
            self.pending_chunk_ack_chunk_indexes = []
            self.pending_chunk_ack_flush_task = None
            return

        self.pending_chunk_ack_request_ids = []
        self.pending_chunk_ack_chunk_indexes = []
        task = self.pending_chunk_ack_flush_task
        if task and task is not asyncio.current_task():
            task.cancel()
        self.pending_chunk_ack_flush_task = None

        await self.send_json(
            {
                "type": "message_chunk_part_response",
                "ok": True,
                "request_id": request_ids[-1],
                "request_ids": request_ids,
                "chunk_indexes": chunk_indexes,
            }
        )

    async def _queue_chunk_ack(self, request_id: int, chunk_index: int):
        self.pending_chunk_ack_request_ids.append(request_id)
        self.pending_chunk_ack_chunk_indexes.append(chunk_index)
        if len(self.pending_chunk_ack_request_ids) >= WS_CHUNK_ACK_BATCH_SIZE:
            await self._flush_chunk_acks()
            return
        if not self.pending_chunk_ack_flush_task:
            self.pending_chunk_ack_flush_task = asyncio.create_task(
                self._delayed_flush_chunk_acks()
            )

    async def handle_message(self, data):
        message_type = data.get("type")
        chat_id = data.get("chat_id")

        if (
            message_type
            in (
                "chat_message",
                "message_reaction",
                "message_viewed",
                "edit_message",
                "delete_message",
                "delete_chat",
                "add_group_member",
                "remove_group_member",
            )
            and not chat_id
        ):
            return await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )

        try:
            if message_type == "chat_message":
                await self.handle_chat_message(data, chat_id)
            elif message_type == "edit_message":
                await self.handle_edit_message(data, data.get("chat_id"))
            elif message_type == "delete_message":
                await self.handle_delete_message(data, data.get("chat_id"))
            elif message_type == "delete_chat":
                await self.handle_delete_chat(data, data.get("chat_id"))
            elif message_type == "add_group_member":
                await self.handle_add_group_member(data, data.get("chat_id"))
            elif message_type == "remove_group_member":
                await self.handle_remove_group_member(data, data.get("chat_id"))
            elif message_type == "message_reaction":
                await self.handle_message_reaction(data)
            elif message_type == "message_viewed":
                await self.handle_message_viewed(data)
            elif message_type == "set_session_lifetime":
                await self.set_session_lifetime(data)
            elif message_type == "get_active_sessions":
                await self.handle_get_active_sessions(data)
            elif message_type == "revoke_session":
                await self.handle_revoke_session_ws(data)
            elif message_type == "revoke_other_sessions":
                await self.handle_revoke_other_sessions_ws()
            elif message_type == "get_chats":
                await self.handle_get_chats()
            elif message_type == "get_chat":
                await self.handle_get_chat(data)
            elif message_type == "get_general_info":
                await self.handle_get_general_info()
            elif message_type == "get_contacts":
                await self.handle_get_contacts()
            elif message_type == "add_contact":
                await self.handle_add_contact(data)
            elif message_type == "refresh_token":
                await self.handle_refresh_token()
            elif message_type == "login":
                await self.handle_login(data)
            elif message_type == "signup":
                await self.handle_signup(data)
            elif message_type == "add_user_wallpaper":
                await self.handle_add_user_wallpaper(data)
            elif message_type == "set_active_wallpaper":
                await self.handle_set_active_wallpaper(data)
            elif message_type == "delete_user_wallpaper":
                await self.handle_delete_user_wallpaper(data)
            elif message_type == "create_group":
                await self.handle_create_group(data)
            elif message_type == "message_chunk_init":
                await self.handle_message_chunk_init(data)
            elif message_type == "message_chunk_part":
                await self.handle_message_chunk_part(data)
            elif message_type == "message_chunk_complete":
                await self.handle_message_chunk_complete(data)
            else:
                logger.warning(f"Unknown message type: {message_type}")
        except Exception as e:
            logger.error(f"Error handling message type '{message_type}': {e}")

    async def handle_chat_message(self, data, chat_id):
        message_content = data.get("data", {}).get("value", "").strip()
        other_user_id = data.get("data", {}).get("user_id")

        if not message_content:
            return await self.send_json(
                {"type": "error", "message": "Message cannot be empty"}
            )

        try:
            chat_id = int(chat_id) if chat_id is not None else None
        except (TypeError, ValueError):
            chat_id = None

        temp_chat_id_for_event = None
        if chat_id is not None and chat_id <= 0:
            if not other_user_id:
                return await self.send_json(
                    {"type": "error", "message": "user_id required"}
                )
            try:
                other_user_id = int(other_user_id)
            except (TypeError, ValueError):
                return await self.send_json(
                    {"type": "error", "message": "user_id must be a number"}
                )

            temp_chat_id_for_event = chat_id
            chat, created = await self.get_or_create_dialog(other_user_id)
            chat_id = chat.id

        if chat_id is None:
            return await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )

        if not await self.user_in_chat(chat_id):
            return await self.send_json(
                {"type": "error", "message": "Not a member of chat"}
            )

        message = await self.save_message(chat_id, self.user, message_content)
        if not message:
            return await self.send_json(
                {"type": "error", "message": "Failed to save message"}
            )

        # New-dialog flow: send chat_created after saving message so payload includes last_message
        if temp_chat_id_for_event is not None:
            user_ids = await self.get_chat_user_ids(chat_id)
            base_chat = await self.serialize_chat(chat)
            for uid in user_ids:
                last_msg = await self.serialize_message_for_recipient(message, uid)
                payload = {
                    "temp_chat_id": temp_chat_id_for_event,
                    "chat": {**base_chat, "last_message": last_msg},
                }
                payload_safe = json.loads(
                    json.dumps(payload, default=str)
                )
                await self.send_to_user_group(uid, "chat_created", **payload_safe)

        await self.broadcast_message_to_chat_users(chat_id, message)

    async def handle_edit_message(self, data, chat_id):
        message_id = data.get("message_id")
        if not message_id:
            await self.send_json(
                {"type": "error", "message": "message_id is required"}
            )
            return
        new_value = (data.get("data") or {}).get("value")
        if new_value is None:
            await self.send_json(
                {"type": "error", "message": "data.value is required"}
            )
            return
        new_value = new_value.strip() if isinstance(new_value, str) else ""
        if not new_value:
            await self.send_json(
                {"type": "error", "message": "Message cannot be empty"}
            )
            return
        chat_id = int(chat_id)
        if not await self.user_in_chat(chat_id):
            await self.send_json(
                {"type": "error", "message": "Not a member of chat"}
            )
            return
        updated_message = await self.update_message(message_id, self.user, new_value)
        if not updated_message:
            await self.send_json(
                {"type": "error", "message": "Message not found or you cannot edit it"}
            )
            return
        await self.broadcast_message_event_to_chat_users(
            chat_id, "message_updated", updated_message, chat_id=chat_id
        )
        # Ensure editor gets message_updated on this connection (broadcast may be delayed)
        serialized_self = await self.serialize_message_for_recipient(
            updated_message, self.user.id
        )
        await self.send_json(
            {"type": "message_updated", "chat_id": chat_id, "data": serialized_self}
        )

    async def handle_delete_message(self, data, chat_id):
        message_id = data.get("message_id")
        if message_id is None:
            await self.send_json(
                {"type": "error", "message": "message_id is required"}
            )
            return
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "message_id must be a number"}
            )
            return
        if chat_id is None:
            await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "chat_id must be a number"}
            )
            return
        if not await self.user_in_chat(chat_id):
            await self.send_json(
                {"type": "error", "message": "Not a member of chat"}
            )
            return
        deleted = await self.delete_message(message_id, self.user)
        if not deleted:
            await self.send_json(
                {"type": "error", "message": "Message not found or you cannot delete it"}
            )
            return
        await self.broadcast_to_chat_users(
            chat_id,
            "message_deleted",
            {"chat_id": chat_id, "message_id": message_id},
        )

    async def handle_delete_chat(self, data, chat_id):
        if chat_id is None:
            await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "chat_id must be a number"}
            )
            return

        if not await self.user_in_chat(chat_id):
            await self.send_json(
                {"type": "error", "message": "Not a member of chat"}
            )
            return

        user_ids = await self.get_chat_user_ids(chat_id)
        deleted = await self.delete_chat(chat_id, self.user)
        if not deleted:
            await self.send_json(
                {"type": "error", "message": "Chat not found or cannot be deleted"}
            )
            return

        payload = {"chat_id": chat_id}
        for user_id in user_ids:
            await self.send_to_user_group(user_id, "chat_deleted", **payload)

    async def handle_add_group_member(self, data, chat_id):
        if chat_id is None:
            await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "chat_id must be a number"}
            )
            return

        new_user_id = (data.get("data") or {}).get("user_id")
        if new_user_id is None:
            await self.send_json(
                {"type": "error", "message": "user_id is required"}
            )
            return
        try:
            new_user_id = int(new_user_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "user_id must be a number"}
            )
            return

        err = await self.try_add_group_member(chat_id, new_user_id)
        if err:
            await self.send_json({"type": "error", "message": err})
            return

        user_ids = await self.get_chat_user_ids(chat_id)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROADCASTS)

        async def send_one(uid):
            async with semaphore:
                body = await self.build_group_members_updated_event(
                    chat_id, uid, new_user_id
                )
                payload_safe = json.loads(json.dumps(body, default=str))
                await self.send_to_user_group(
                    uid,
                    "group_members_updated",
                    **payload_safe,
                )

        await asyncio.gather(*(send_one(uid) for uid in user_ids))

    async def handle_remove_group_member(self, data, chat_id):
        if chat_id is None:
            await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "chat_id must be a number"}
            )
            return

        target_user_id = (data.get("data") or {}).get("user_id")
        if target_user_id is None:
            await self.send_json(
                {"type": "error", "message": "user_id is required"}
            )
            return
        try:
            target_user_id = int(target_user_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "user_id must be a number"}
            )
            return

        err, payload = await self.try_remove_group_member(chat_id, target_user_id)
        if err:
            await self.send_json({"type": "error", "message": err})
            return

        removed_id = payload["removed_id"]
        remaining_ids = payload["remaining_ids"]

        await self.send_to_user_group(removed_id, "chat_deleted", chat_id=chat_id)

        if not remaining_ids:
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROADCASTS)

        async def send_one(uid):
            async with semaphore:
                body = await self.build_group_members_updated_event(
                    chat_id, uid, None
                )
                payload_safe = json.loads(json.dumps(body, default=str))
                await self.send_to_user_group(
                    uid,
                    "group_members_updated",
                    **payload_safe,
                )

        await asyncio.gather(*(send_one(uid) for uid in remaining_ids))

    async def handle_create_group(self, data):
        raw = (data.get("data") or {}).get("name")
        if raw is None:
            await self.send_json(
                {"type": "error", "message": "name is required"}
            )
            return
        name = str(raw).strip()
        try:
            serialized = await database_sync_to_async(create_group_and_serialize)(
                self.user, name
            )
            chat_id = serialized["id"]
            self.chat_users_cache[chat_id] = [self.user.id]
            payload_safe = json.loads(json.dumps({"chat": serialized}, default=str))
            await self.send_to_user_group(
                self.user.id, "chat_created", **payload_safe
            )
        except ValueError as e:
            await self.send_json({"type": "error", "message": str(e)})
        except Exception as e:
            logger.exception("create_group failed: %s", e)
            await self.send_json(
                {
                    "type": "error",
                    "message": "Failed to create group",
                }
            )

    async def handle_message_reaction(self, data):
        message_id = data.get("message_id")
        if not message_id:
            await self.send_json(
                {"type": "error", "message": "message_id is required"}
            )
            return

        reaction_type = data.get("data", {}).get("reaction_type")
        valid_reactions = {
            choice[0] for choice in MessageReaction.REACTION_TYPES
        }
        if reaction_type not in valid_reactions:
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Invalid reaction_type. Allowed: {', '.join(sorted(valid_reactions))}",
                }
            )
            return

        chat_id = await self.get_message_chat_id(message_id)
        if not chat_id or not await self.user_in_chat(chat_id):
            await self.send_json(
                {"type": "error", "message": "Message not found or no access"}
            )
            return

        updated_message, error_message = await self.update_message_reaction(
            message_id, self.user, reaction_type
        )
        if error_message:
            await self.send_json(
                {"type": "error", "message": error_message}
            )
            return

        await self.broadcast_message_event_to_chat_users(
            chat_id,
            "message_reaction",
            updated_message,
            message_id=message_id,
            chat_id=chat_id,
            reaction_type=reaction_type,
            actor_user_id=self.user.id,
        )

    async def handle_message_viewed(self, data):
        message_id = data.get("message_id")
        chat_id = data.get("chat_id")
        if not message_id:
            return

        if not chat_id:
            chat_id = await self.get_message_chat_id(message_id)

        await self.mark_message_as_viewed(message_id, self.user.id)
        await self.broadcast_to_chat_users(
            chat_id,
            "message_viewed",
            {"chat_id": chat_id, "message_id": message_id, "userId": self.user.id},
        )

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

    async def handle_get_active_sessions(self, data):
        request_id = data.get("request_id")
        current_jti = self.scope.get("access_jti")
        try:
            sessions = await database_sync_to_async(
                serialize_active_sessions_for_ws_user
            )(self.user, current_jti)
        except Exception as e:
            logger.exception("get_active_sessions failed: %s", e)
            await self.send_json(
                {
                    "type": "error",
                    "message": "Failed to load active sessions",
                    "code": "active_sessions",
                    "request_id": request_id,
                }
            )
            return
        await self.send_json(
            {
                "type": "active_sessions",
                "sessions": sessions,
                "request_id": request_id,
            }
        )

    async def handle_revoke_session_ws(self, data):
        jti = data.get("jti")
        if not jti:
            return await self.send_json(
                {"type": "error", "message": "jti is required"}
            )
        current_jti = self.scope.get("access_jti")
        err = await database_sync_to_async(revoke_active_session_for_user)(
            self.user, str(jti), current_jti
        )
        if err == "not_found":
            return await self.send_json(
                {"type": "error", "message": "Session not found"}
            )
        if err == "cannot_revoke_current":
            return await self.send_json(
                {"type": "error", "message": "Cannot revoke current session"}
            )

    async def handle_revoke_other_sessions_ws(self):
        current_jti = self.scope.get("access_jti")
        await database_sync_to_async(revoke_other_active_sessions_for_user)(
            self.user, current_jti
        )

    async def handle_get_chats(self):
        try:
            result = await database_sync_to_async(get_chats_list)(self.user)
            await self.send_json({"type": "chats", "chats": result["chats"]})
        except Exception as e:
            logger.exception("get_chats failed: %s", e)
            from django.conf import settings
            message = str(e) if settings.DEBUG else "Failed to load chats"
            await self.send_json({"type": "error", "message": message})

    async def handle_get_chat(self, data):
        chat_id = data.get("chat_id")
        if chat_id is None:
            await self.send_json(
                {"type": "error", "message": "chat_id is required"}
            )
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "chat_id must be a number"}
            )
            return
        try:
            cursor = data.get("cursor")
            if cursor is not None:
                try:
                    cursor = int(cursor)
                except (TypeError, ValueError):
                    cursor = None
            cursor_newer = data.get("cursor_newer")
            if cursor_newer is not None:
                try:
                    cursor_newer = int(cursor_newer)
                except (TypeError, ValueError):
                    cursor_newer = None
            page_size = data.get("page_size", 25)
            if not isinstance(page_size, int):
                try:
                    page_size = int(page_size) if page_size is not None else 25
                except (TypeError, ValueError):
                    page_size = 25
            result = await database_sync_to_async(get_chat_for_user)(
                chat_id,
                self.user,
                cursor=cursor,
                cursor_newer=cursor_newer,
                page_size=page_size,
            )
            await self.send_json({"chat_id": chat_id, **result, "type": "chat"})
        except Chat.DoesNotExist:
            await self.send_json(
                {"type": "error", "message": "Chat not found"}
            )
        except Exception as e:
            logger.exception("get_chat failed: %s", e)
            from django.conf import settings
            message = str(e) if settings.DEBUG else "Failed to load chat"
            await self.send_json({"type": "error", "message": message})

    async def handle_get_general_info(self):
        try:
            result = await database_sync_to_async(get_general_info_for_user)(
                self.user
            )
            await self.send_json({"type": "general_info", **result})
        except Exception as e:
            logger.exception("get_general_info failed: %s", e)
            from django.conf import settings
            message = str(e) if settings.DEBUG else "Failed to load general info"
            await self.send_json({"type": "error", "message": message})

    async def handle_get_contacts(self):
        try:
            result = await database_sync_to_async(get_contacts_for_user)(
                self.user
            )
            await self.send_json({"type": "contacts", **result})
        except Exception as e:
            logger.exception("get_contacts failed: %s", e)
            from django.conf import settings
            message = str(e) if settings.DEBUG else "Failed to load contacts"
            await self.send_json({"type": "error", "message": message})

    async def handle_add_contact(self, data):
        new_user_id = (data.get("data") or {}).get("user_id")
        if new_user_id is None:
            await self.send_json(
                {"type": "error", "message": "user_id is required"}
            )
            return
        try:
            new_user_id = int(new_user_id)
        except (TypeError, ValueError):
            await self.send_json(
                {"type": "error", "message": "user_id must be a number"}
            )
            return

        try:
            payload, err = await database_sync_to_async(_add_contact_via_ws)(
                self.user.pk, new_user_id
            )
        except Exception as e:
            logger.exception("add_contact failed: %s", e)
            from django.conf import settings

            message = str(e) if settings.DEBUG else "Failed to add contact"
            await self.send_json({"type": "error", "message": message})
            return

        if err:
            await self.send_json({"type": "error", "message": err})
            return
        await self.send_json(payload)

    def _get_refresh_token_from_scope(self):
        for key, value in self.scope.get("headers", []):
            if key == b"cookie":
                cookies = {}
                for item in value.decode().split("; "):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        cookies[k.strip()] = v.strip()
                return cookies.get("refresh_token")
        return None

    def _refresh_access_from_refresh_token(self, token_str):
        """Issue a new access JWT for the current session (same jti as TokenAuthMiddleware on connect)."""
        try:
            refresh = RefreshToken(token_str)
            jti = str(refresh["jti"])
            session = ActiveSession.objects.filter(
                jti=jti, expires_at__gt=timezone.now()
            ).first()
            if not session:
                return None
            if not session_binding_matches_session(session, scope=self.scope):
                return None
            return get_access_token_for_session(
                jti, session.user, session.binding_hash
            )
        except Exception:
            return None

    async def handle_refresh_token(self):
        token_str = self._get_refresh_token_from_scope()
        if not token_str:
            await self.send_json(
                {"type": "error", "message": "No refresh token"}
            )
            return
        try:
            access = await database_sync_to_async(
                self._refresh_access_from_refresh_token
            )(token_str)
            if access:
                await self.send_json(
                    {"type": "refresh_token_response", "access": access}
                )
            else:
                await self.send_json(
                    {"type": "error", "message": "Invalid refresh token"}
                )
        except Exception as e:
            logger.exception("refresh_token failed: %s", e)
            await self.send_json(
                {"type": "error", "message": "Invalid refresh token"}
            )

    def _do_login(self, username_or_email, password, backup_code="", totp_code=""):
        from django.contrib.auth import authenticate

        user = authenticate(username=username_or_email, password=password)
        if not user:
            return None
        if not user.email_verified_at:
            return {
                "error": "email_not_verified",
                "email": user.email,
            }
        if user.totp_enabled:
            tc = (totp_code or "").strip()
            if not tc:
                return {"error": "totp_required"}
            if not user_totp_gate_ok(user, tc):
                return {"error": "invalid_totp"}
        binding = binding_from_scope(self.scope)
        challenge_binding = stable_device_login_challenge_binding_from_scope(
            self.scope
        )
        if not binding_matches_trusted(user, binding):
            if backup_code:
                if not verify_and_consume_backup_code(user, backup_code):
                    return {"error": "invalid_backup_code"}
                User = get_user_model()
                User.objects.filter(pk=user.pk).update(trusted_binding_hash=binding)
                user.trusted_binding_hash = binding
            else:
                req_ip, req_ua = ip_and_user_agent_from_scope(self.scope)
                gate = deferred_login_payload(
                    user,
                    binding,
                    device_challenge_binding_hash=challenge_binding,
                    request_ip=req_ip,
                    request_user_agent=req_ua,
                )
                if gate:
                    return gate
        refresh = create_refresh_token_for_user(user)
        ws_session = remember_session_from_scope(self.scope, user, refresh)
        ensure_trusted_from_session_binding(user, ws_session.binding_hash)
        jti = str(refresh["jti"])
        access = get_access_token_for_session(jti, user, ws_session.binding_hash)
        user_data = UserSerializer(user, context={"user": user}).data
        return {"access": access, "refresh": str(refresh), "user": user_data, "jti": jti, "user_obj": user}

    def _do_signup(self, username, email, password):
        from django.db import IntegrityError
        from django.contrib.auth import get_user_model

        User = get_user_model()
        try:
            user = User.objects.create_user(
                email=email, password=password, username=username or email
            )
        except IntegrityError:
            return None
        ev_otp, plain = create_email_verification_otp(user)
        try:
            send_email_verification_code_email(user, plain)
        except Exception:
            logger.exception("verification email failed for WS signup user %s", user.pk)
        return {
            "needs_email_verification": True,
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "email_verification_otp_id": str(ev_otp.id),
        }

    async def handle_login(self, data):
        try:
            identifier = (data.get("email") or data.get("username") or "").strip()
            password = data.get("password") or ""
            if not identifier or not password:
                await self.send_json(
                    {"type": "login_response", "error": "username and password required"}
                )
                return
            backup_code = (data.get("backup_code") or "").strip()
            totp_code = (data.get("totp_code") or "").strip()
            result = await database_sync_to_async(self._do_login)(
                identifier, password, backup_code, totp_code
            )
            if not result:
                await self.send_json(
                    {"type": "login_response", "error": "Invalid credentials"}
                )
                return
            if result.get("error") == "email_not_verified":
                await self.send_json(
                    {
                        "type": "login_response",
                        "error": "email_not_verified",
                        "email": result.get("email"),
                    }
                )
                return
            if result.get("error") == "invalid_backup_code":
                await self.send_json(
                    {"type": "login_response", "error": "invalid_backup_code"}
                )
                return
            if result.get("error") == "totp_required":
                await self.send_json(
                    {"type": "login_response", "error": "totp_required"}
                )
                return
            if result.get("error") == "invalid_totp":
                await self.send_json(
                    {"type": "login_response", "error": "invalid_totp"}
                )
                return
            if result.get("needs_device_confirmation"):
                await self.send_json(
                    {
                        "type": "login_response",
                        "needs_device_confirmation": True,
                        "challenge_id": result["challenge_id"],
                        "trusted_device": result.get("trusted_device") or "",
                    }
                )
                return
            user = result["user_obj"]
            self.scope["user"] = user
            self.user = user
            self.scope["auth_valid"] = True
            self.scope["access_jti"] = result["jti"]
            self.user_group_name = f"user_{user.id}"
            self.session_group_name = f"session_{result['jti']}"
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
            await self.channel_layer.group_add(
                self.session_group_name, self.channel_name
            )
            await self.send_json(
                {
                    "type": "login_response",
                    "access": result["access"],
                    "refresh": result["refresh"],
                    "user": result["user"],
                }
            )
        except Exception as e:
            logger.exception("handle_login failed: %s", e)
            await self.send_json(
                {"type": "login_response", "error": str(e) or "Login failed"}
            )

    async def handle_signup(self, data):
        username = (data.get("username") or "").strip()
        email = (data.get("email") or "").strip()
        password = data.get("password") or ""
        if not email or not password:
            await self.send_json(
                {"type": "signup_response", "error": "email and password required"}
            )
            return
        result = await database_sync_to_async(self._do_signup)(
            username or email, email, password
        )
        if not result:
            await self.send_json(
                {"type": "signup_response", "error": "User already exists"}
            )
            return
        if result.get("needs_email_verification"):
            await self.send_json(
                {
                    "type": "signup_response",
                    "needs_email_verification": True,
                    "user_id": result["user_id"],
                    "username": result["username"],
                    "email": result["email"],
                    "email_verification_otp_id": result.get(
                        "email_verification_otp_id"
                    ),
                }
            )
            return
        user = result["user_obj"]
        self.scope["user"] = user
        self.user = user
        self.scope["auth_valid"] = True
        self.scope["access_jti"] = result["jti"]
        self.user_group_name = f"user_{user.id}"
        self.session_group_name = f"session_{result['jti']}"
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.channel_layer.group_add(
            self.session_group_name, self.channel_name
        )
        await self.send_json(
            {
                "type": "signup_response",
                "access": result["access"],
                "refresh": result["refresh"],
                "user": result["user"],
            }
        )

    @database_sync_to_async
    def try_add_group_member(self, chat_id, new_user_id):
        User = get_user_model()
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            return "Chat not found"
        if not chat.is_group:
            return "Not a group chat"
        if not chat.users.filter(id=self.user.id).exists():
            return "Not a member of chat"
        if new_user_id == self.user.id:
            return "Cannot add yourself"
        try:
            new_user = User.objects.get(id=new_user_id)
        except User.DoesNotExist:
            return "User not found"
        if chat.users.filter(id=new_user_id).exists():
            return "User is already in this chat"
        ChatMember.objects.create(
            chat=chat, user=new_user, role=ChatMember.Role.MEMBER
        )
        self.chat_users_cache.pop(chat_id, None)
        return None

    @database_sync_to_async
    def build_group_members_updated_event(
        self, chat_id, recipient_id, invitee_user_id=None
    ):
        User = get_user_model()
        recipient = User.objects.select_related("profile").get(id=recipient_id)
        chat = Chat.objects.prefetch_related(
            "users",
            "users__profile",
            "users__profile__profile_media",
        ).get(id=chat_id)
        users_count = chat.users.count()
        context = {"user": recipient, "user_id": recipient.id}
        qs = chat.users.all().exclude(pk=recipient.pk)
        members_data = ChatUserSerializer(qs, many=True, context=context).data
        out = {
            "chat_id": chat_id,
            "members": members_data,
            "users_count": users_count,
        }
        if (
            invitee_user_id is not None
            and recipient_id == invitee_user_id
        ):
            lst = get_chats_list(recipient, chat_ids=[chat_id])
            rows = lst.get("chats") or []
            if rows:
                out["chat"] = rows[0]
        return out

    @database_sync_to_async
    def try_remove_group_member(self, chat_id, target_user_id):
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            return "Chat not found", None
        if not chat.is_group:
            return "Not a group chat", None
        if not chat.users.filter(id=self.user.id).exists():
            return "Not a member of chat", None
        if not chat.users.filter(id=target_user_id).exists():
            return "User is not in this chat", None

        ChatMember.objects.filter(chat_id=chat_id, user_id=target_user_id).delete()
        self.chat_users_cache.pop(chat_id, None)

        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            return None, {"removed_id": target_user_id, "remaining_ids": []}

        remaining_ids = list(chat.users.values_list("id", flat=True))
        if not remaining_ids:
            chat.delete()
        return None, {
            "removed_id": target_user_id,
            "remaining_ids": remaining_ids,
        }

    @database_sync_to_async
    def get_or_create_dialog(self, other_user_id):
        User = get_user_model()

        other_user = User.objects.get(id=other_user_id)
        return Chat.get_or_create_direct_chat(self.user, other_user)

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
    def update_message(self, message_id, user, new_value):
        """Update message text if the user is the author. Returns updated message or None."""
        try:
            message = Message.objects.select_related("user", "user__profile", "reply_to").prefetch_related(
                "file", "message_reactions"
            ).filter(id=message_id).first()
            if not message or message.user_id != user.id:
                return None
            message.value = new_value
            message.edit_date = timezone.now()
            message.save(update_fields=["value", "edit_date"])
            return (
                Message.objects.filter(id=message.id)
                .select_related("user", "user__profile", "reply_to")
                .prefetch_related("file", "message_reactions")
                .first()
            )
        except Exception as e:
            logger.error(f"Error updating message: {e}")
            return None

    @database_sync_to_async
    def delete_message(self, message_id, user):
        """Soft-delete message if the user is the author. Returns True on success."""
        try:
            message = Message.objects.filter(id=message_id).first()
            if not message or message.user_id != user.id:
                return False
            message.value = None
            message.deleted_at = timezone.now()
            message.save(update_fields=["value", "deleted_at"])
            return True
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            return False

    @database_sync_to_async
    def delete_chat(self, chat_id, user):
        try:
            chat = Chat.objects.filter(id=chat_id, users=user).first()
            if not chat:
                return False
            chat.delete()
            self.chat_users_cache.pop(chat_id, None)
            return True
        except Exception as e:
            logger.error(f"Error deleting chat: {e}")
            return False

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
                return None, "Message not found"
            try:
                message.set_user_reaction(user, reaction_type)
            except ValueError as e:
                return None, str(e)
            updated = (
                Message.objects.filter(id=message_id)
                .select_related("user", "user__profile", "reply_to")
                .prefetch_related("file", "message_reactions")
                .first()
            )
            return updated, None
        except Exception as e:
            logger.error(f"Error updating message reaction: {e}")
            return None, "Failed to update reaction"

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
    def serialize_message_for_recipient(self, message, recipient_id):
        """Serialize message so is_own is True only for the recipient who is the author."""
        serializer = MessageSerializer(message, context={"user_id": recipient_id})
        return serializer.data

    @database_sync_to_async
    def serialize_chat(self, chat):
        """Serialize chat for chat_created event. Includes members and safe context for single chat."""
        context = {
            "user": self.user,
            "user_id": self.user.id,
            "interlocutors_map": {chat.pk: chat.get_interlocutor(self.user)},
            "contacts_map": {},
            "media_map": {},
        }
        list_data = ChatListSerializer(chat, context=context).data
        # ChatListSerializer does not include members; frontend expects them
        other_users = list(chat.users.exclude(pk=self.user.pk))
        list_data["members"] = ChatUserSerializer(
            other_users, many=True, context=context
        ).data
        return list_data

    async def broadcast_to_chat_users(self, chat_id, event_type, payload):
        user_ids = await self.get_chat_user_ids(chat_id)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROADCASTS)

        async def send_to_user(user_id):
            async with semaphore:
                await self.send_to_user_group(user_id, event_type, **payload)

        await asyncio.gather(*(send_to_user(uid) for uid in user_ids))

    async def broadcast_message_to_chat_users(self, chat_id, message):
        """Send chat_message to each chat user with message serialized per recipient (is_own correct for each)."""
        user_ids = await self.get_chat_user_ids(chat_id)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROADCASTS)

        async def send_to_user(recipient_id):
            async with semaphore:
                serialized = await self.serialize_message_for_recipient(
                    message, recipient_id
                )
                await self.send_to_user_group(
                    recipient_id,
                    "chat_message",
                    chat_id=chat_id,
                    data=serialized,
                )

        await asyncio.gather(*(send_to_user(uid) for uid in user_ids))

    async def broadcast_message_event_to_chat_users(
        self, chat, event_type, message, **payload_extra
    ):
        """Broadcast an event containing a serialized message, with is_own correct per recipient."""
        user_ids = await self.get_chat_user_ids(chat)
        channel_layer = get_channel_layer()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROADCASTS)

        async def send_to_user(recipient_id):
            async with semaphore:
                serialized = await self.serialize_message_for_recipient(
                    message, recipient_id
                )
                payload = {"type": event_type, "data": serialized, **payload_extra}
                try:
                    payload_safe = json.loads(
                        json.dumps(payload, default=str)
                    )
                except (TypeError, ValueError):
                    payload_safe = payload
                await channel_layer.group_send(
                    f"user_{recipient_id}",
                    payload_safe,
                )

        await asyncio.gather(*(send_to_user(uid) for uid in user_ids))

    async def chat_message(self, event):
        await self.send_json(event)

    async def message_updated(self, event):
        await self.send_json(event)

    async def message_deleted(self, event):
        await self.send_json(event)

    async def chat_deleted(self, event):
        await self.send_json(
            {
                "type": "chat_deleted",
                "chat_id": event.get("chat_id"),
            }
        )

    async def message_reaction(self, event):
        await self.send_json(event)

    async def message_viewed(self, event):
        await self.send_json(event)

    async def session_created(self, event):
        await self.send_json(event)

    async def session_lifetime_updated(self, event):
        await self.send_json(event)

    async def session_deleted(self, event):
        await self.send_json(
            {
                "type": "session_deleted",
                "session": event.get("session", {}),
            }
        )

    async def device_login_pending(self, event):
        await self.send_json(
            {
                "type": "device_login_pending",
                "challenge_id": event.get("challenge_id"),
                "request_ip": event.get("request_ip") or "",
                "request_user_agent": event.get("request_user_agent") or "",
                "request_city": event.get("request_city") or "",
                "request_country": event.get("request_country") or "",
                "request_device": event.get("request_device") or "",
            }
        )

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

    async def group_members_updated(self, event):
        await self.send_json(
            {
                "type": "group_members_updated",
                "chat_id": event.get("chat_id"),
                "members": event.get("members"),
                "users_count": event.get("users_count"),
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

    async def handle_message_chunk_init(self, data):
        from apps.Site.message_chunk_upload_views import (
            WS_SERVER_CHUNK_SIZE,
            chunk_init_service,
        )

        request_id = data.get("request_id")
        d = data.get("data") or {}
        try:
            chat_id = int(d.get("chat_id") or 0)
        except (TypeError, ValueError):
            await self.send_json(
                {
                    "type": "message_chunk_init_response",
                    "request_id": request_id,
                    "ok": False,
                    "error": "Invalid chat_id",
                }
            )
            return

        filename = (d.get("filename") or "file").replace("\\", "/").split("/")[-1]
        mime_type = (d.get("mime_type") or "").strip().lower()
        media_kind = (d.get("media_kind") or "").strip().lower()
        try:
            total_size = int(d.get("total_size") or 0)
        except (TypeError, ValueError):
            await self.send_json(
                {
                    "type": "message_chunk_init_response",
                    "request_id": request_id,
                    "ok": False,
                    "error": "Invalid total_size",
                }
            )
            return

        raw_chunk = d.get("chunk_size")
        try:
            requested_chunk = int(raw_chunk) if raw_chunk is not None else None
        except (TypeError, ValueError):
            await self.send_json(
                {
                    "type": "message_chunk_init_response",
                    "request_id": request_id,
                    "ok": False,
                    "error": "Invalid chunk_size",
                }
            )
            return

        ws_chunk = (
            requested_chunk if requested_chunk is not None else WS_SERVER_CHUNK_SIZE
        )

        result = await sync_to_async(chunk_init_service)(
            self.user,
            chat_id,
            filename,
            mime_type,
            media_kind,
            total_size,
            chunk_size=ws_chunk,
            width=d.get("width"),
            height=d.get("height"),
        )
        payload: dict = {"type": "message_chunk_init_response", "request_id": request_id}
        if result.get("ok"):
            payload.update(
                ok=True,
                upload_id=result["upload_id"],
                chunk_count=result["chunk_count"],
                chunk_size=result["chunk_size"],
            )
        else:
            payload.update(ok=False, error=result.get("error", "error"))
        await self.send_json(payload)

    async def handle_message_chunk_part(self, data):
        from apps.Site.message_chunk_upload_views import chunk_part_service

        request_id = data.get("request_id")
        d = data.get("data") or {}
        upload_id = d.get("upload_id")
        try:
            chunk_index = int(d.get("chunk_index", -1))
        except (TypeError, ValueError):
            await self.send_json(
                {
                    "type": "message_chunk_part_response",
                    "request_id": request_id,
                    "ok": False,
                    "error": "Invalid chunk_index",
                }
            )
            return

        chunk_b64 = d.get("chunk_b64")
        if isinstance(chunk_b64, str):
            import base64
            import binascii

            try:
                raw = base64.b64decode(chunk_b64, validate=True)
            except (ValueError, binascii.Error):
                await self.send_json(
                    {
                        "type": "message_chunk_part_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": "Invalid base64 chunk",
                    }
                )
                return

            result = await sync_to_async(chunk_part_service)(
                self.user, upload_id, chunk_index, raw
            )
            payload: dict = {
                "type": "message_chunk_part_response",
                "request_id": request_id,
            }
            if result.get("ok"):
                payload.update(ok=True, chunk_index=result["chunk_index"])
            else:
                payload.update(ok=False, error=result.get("error", "error"))
            await self.send_json(payload)
            return

        if not isinstance(request_id, int):
            await self.send_json(
                {
                    "type": "message_chunk_part_response",
                    "request_id": request_id,
                    "ok": False,
                    "error": "request_id required",
                }
            )
            return

        # Binary path: metadata arrives as JSON, raw bytes follow in next binary frame.
        self.pending_chunk_parts[request_id] = {
            "upload_id": upload_id,
            "chunk_index": chunk_index,
        }

    async def handle_binary(self, bytes_data):
        from apps.Site.message_chunk_upload_views import chunk_part_service

        if not bytes_data or len(bytes_data) < 4:
            await self.send_json(
                {
                    "type": "message_chunk_part_response",
                    "ok": False,
                    "error": "Invalid binary chunk frame",
                }
            )
            return

        request_id = int.from_bytes(bytes_data[:4], byteorder="big", signed=False)
        meta = self.pending_chunk_parts.pop(request_id, None)
        if meta:
            raw = bytes_data[4:]
            result = await sync_to_async(chunk_part_service)(
                self.user, meta.get("upload_id"), int(meta.get("chunk_index", -1)), raw
            )
        else:
            # New binary-only protocol:
            # [request_id:4][chunk_index:4][upload_id_len:2][upload_id:utf-8][chunk_bytes]
            if len(bytes_data) < 10:
                await self.send_json(
                    {
                        "type": "message_chunk_part_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": "Invalid binary chunk frame",
                    }
                )
                return
            chunk_index = int.from_bytes(bytes_data[4:8], byteorder="big", signed=False)
            upload_id_len = int.from_bytes(bytes_data[8:10], byteorder="big", signed=False)
            upload_id_end = 10 + upload_id_len
            if upload_id_len < 1 or upload_id_end > len(bytes_data):
                await self.send_json(
                    {
                        "type": "message_chunk_part_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": "Invalid binary chunk frame",
                    }
                )
                return
            try:
                upload_id = bytes_data[10:upload_id_end].decode("utf-8")
            except UnicodeDecodeError:
                await self.send_json(
                    {
                        "type": "message_chunk_part_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": "Invalid upload_id encoding",
                    }
                )
                return
            raw = bytes_data[upload_id_end:]
            result = await sync_to_async(chunk_part_service)(
                self.user, upload_id, chunk_index, raw
            )

        if result.get("ok"):
            await self._queue_chunk_ack(request_id, int(result["chunk_index"]))
            return

        await self.send_json(
            {
                "type": "message_chunk_part_response",
                "request_id": request_id,
                "ok": False,
                "error": result.get("error", "error"),
            }
        )

    async def handle_message_chunk_complete(self, data):
        from apps.Site.message_chunk_upload_views import chunk_bundle_complete_service

        request_id = data.get("request_id")
        d = data.get("data") or {}
        try:
            chat_id = int(d.get("chat_id") or 0)
        except (TypeError, ValueError):
            await self.send_json(
                {
                    "type": "message_chunk_complete_response",
                    "request_id": request_id,
                    "ok": False,
                    "error": "Invalid chat_id",
                }
            )
            return

        message_text = d.get("message") or ""
        upload_ids = d.get("upload_ids") or []
        if not isinstance(upload_ids, list):
            upload_ids = []

        result = await sync_to_async(chunk_bundle_complete_service)(
            self.user, chat_id, message_text, upload_ids
        )
        payload: dict = {
            "type": "message_chunk_complete_response",
            "request_id": request_id,
        }
        if result.get("ok"):
            payload.update(
                ok=True,
                status=result["status"],
                message=result["message"],
                message_id=result["message_id"],
            )
        else:
            payload.update(ok=False, error=result.get("error", "error"))
        await self.send_json(payload)
