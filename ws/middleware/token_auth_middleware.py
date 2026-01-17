# apps/ws/middleware.py

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken, TokenError

User = get_user_model()


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope["user"] = AnonymousUser()
        scope["auth_valid"] = False
        scope["access_jti"] = None

        token = self._get_token_from_scope(scope)

        if token:
            user_and_jti = await self._get_user_and_jti(token)
            if user_and_jti:
                user, jti = user_and_jti
                scope["user"] = user
                scope["auth_valid"] = True
                scope["access_jti"] = jti

        return await super().__call__(scope, receive, send)

    def _get_token_from_scope(self, scope):
        # query string ?token=xxx
        query_string = scope.get("query_string", b"").decode()
        for pair in query_string.split("&"):
            if pair.startswith("token="):
                return pair.split("=", 1)[1]

        # cookie access_token
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                cookies = dict(
                    item.split("=", 1) for item in v.decode().split("; ") if "=" in item
                )
                return cookies.get("access_token")

        return None

    @database_sync_to_async
    def _get_user_and_jti(self, token_str):
        try:
            token = AccessToken(token_str)
            user_id = token["user_id"]
            jti = token["jti"]

            user = User.objects.filter(id=user_id).first()
            if not user:
                return None

            return user, jti
        except (TokenError, KeyError):
            return None
