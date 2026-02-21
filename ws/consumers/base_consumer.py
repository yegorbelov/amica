import json
import logging
import traceback

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, TokenError

from apps.accounts.models.models import ActiveSession

User = get_user_model()

logger = logging.getLogger(__name__)


class BaseConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        try:
            self.user = self.scope.get("user")
            self.session_jti = self.scope.get("access_jti")
            if not self.scope.get("auth_valid") or self.user.is_anonymous:
                logger.warning("WebSocket connection rejected: Unauthorized")
                await self.close(code=4001)
                return

            self.user_group_name = f"user_{self.user.id}"
            self.session_group_name = f"session_{self.session_jti}"

            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
            await self.channel_layer.group_add(
                self.session_group_name, self.channel_name
            )

            await self.accept()
            await self.send(
                json.dumps(
                    {
                        "type": "connection_established",
                        "user_id": self.user.id,
                        "session_jti": self.session_jti,
                    }
                )
            )
        except Exception:
            logger.exception("WebSocket connect error")
            await self.close(code=4002)

    async def disconnect(self, close_code):
        try:
            if hasattr(self, "session_group_name"):
                await self.channel_layer.group_discard(
                    self.session_group_name, self.channel_name
                )
            if hasattr(self, "user_group_name"):
                await self.channel_layer.group_discard(
                    self.user_group_name, self.channel_name
                )
        except Exception:
            logger.exception("Disconnection error")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_json({"type": "error", "message": "Invalid JSON format"})
            return

        if data.get("type") == "auth":
            token = data.get("token")
            user = await self._get_user_from_access_token(token)
            if not user:
                await self.close(code=4001)
                return

            self.scope["user"] = user
            self.user = user
            self.scope["auth_valid"] = True

            self.session_jti = await self.get_user_active_session_jti(user)
            if not self.session_jti:
                await self.close(code=4003)
                return
            self.scope["access_jti"] = self.session_jti

            self.user_group_name = f"user_{self.user.id}"
            self.session_group_name = f"session_{self.session_jti}"
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
            await self.channel_layer.group_add(
                self.session_group_name, self.channel_name
            )

            await self.send_json(
                {
                    "type": "connection_established",
                    "user_id": self.user.id,
                    "session_jti": self.session_jti,
                }
            )
            return

        if not self.scope.get("auth_valid") or not await self._is_session_active():
            await self.close(code=4003)
            return

        await self.touch_session()

        try:
            await self.handle_message(data)
        except Exception as e:
            logger.error("Error in handle_message: %s\n%s", e, traceback.format_exc())
            await self.send_json({"type": "error", "message": "Internal server error"})

    @database_sync_to_async
    def get_user_active_session_jti(self, user):

        session = ActiveSession.objects.filter(
            user=user, expires_at__gt=timezone.now()
        ).first()
        if session:
            return session.jti
        return None

    async def handle_message(self, data):
        raise NotImplementedError("handle_message must be implemented in subclass")

    @database_sync_to_async
    def _is_session_active(self):
        jti = self.scope.get("access_jti")
        if not jti:
            return False
        session = ActiveSession.objects.filter(jti=jti).first()
        if not session:
            return False
        return session.expires_at > timezone.now()

    @database_sync_to_async
    def _get_user_from_access_token(self, token: str):
        try:
            access_token = AccessToken(token)
            user_id = access_token["user_id"]
            user = User.objects.filter(id=user_id, is_active=True).first()
            return user
        except (TokenError, KeyError):
            return None

    @database_sync_to_async
    def touch_session(self):
        jti = self.scope.get("access_jti")
        if not jti:
            return
        session = ActiveSession.objects.filter(jti=jti).first()
        if session:
            session.last_active = timezone.now()
            session.save(update_fields=["last_active"])

    async def send_group(self, group_name, type_, **kwargs):
        await self.channel_layer.group_send(group_name, {"type": type_, **kwargs})

    async def send_to_user_group(self, user_id, type_, **kwargs):
        await self.send_group(f"user_{user_id}", type_, **kwargs)

    async def send_to_session_group(self, session_jti, type_, **kwargs):
        await self.send_group(f"session_{session_jti}", type_, **kwargs)

    async def send_json(self, data):
        await self.send(text_data=json.dumps(data, ensure_ascii=False))
