# apps/ws/middleware.py

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken, TokenError

from apps.accounts.models.models import ActiveSession
from apps.accounts.session_binding import JWT_BINDING_CLAIM, session_binding_matches_session
from apps.accounts.views import get_access_token_for_session

User = get_user_model()


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope["user"] = AnonymousUser()
        scope["auth_valid"] = False
        scope["access_jti"] = None
        scope["issued_access_token"] = None

        token = self._get_token_from_scope(scope)

        if token:
            user_and_jti = await self._get_user_and_jti(token, scope)
            if user_and_jti:
                user, jti = user_and_jti
                scope["user"] = user
                scope["auth_valid"] = True
                scope["access_jti"] = jti
        else:
            refresh_str = self._get_refresh_token_from_scope(scope)
            if refresh_str:
                user_jti_and_access = await self._get_user_and_issue_access(
                    refresh_str, scope
                )
                if user_jti_and_access:
                    user, jti, access = user_jti_and_access
                    scope["user"] = user
                    scope["auth_valid"] = True
                    scope["access_jti"] = jti
                    scope["issued_access_token"] = access

        return await super().__call__(scope, receive, send)

    def _get_refresh_token_from_scope(self, scope):
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                cookies = dict(
                    item.split("=", 1) for item in v.decode().split("; ") if "=" in item
                )
                return cookies.get("refresh_token")
        return None

    @database_sync_to_async
    def _get_user_and_issue_access(self, refresh_str, scope):
        try:
            refresh = RefreshToken(refresh_str)
            jti = str(refresh["jti"])
            session = ActiveSession.objects.filter(
                jti=jti, expires_at__gt=timezone.now()
            ).first()
            if not session:
                return None
            if not session_binding_matches_session(session, scope=scope):
                return None
            user = session.user
            access = get_access_token_for_session(jti, user, session.binding_hash)
            return user, jti, access
        except (TokenError, KeyError):
            return None

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
    def _get_user_and_jti(self, token_str, scope):
        try:
            token = AccessToken(token_str)
            user_id = token["user_id"]
            jti = token["jti"]

            user = User.objects.filter(id=user_id).first()
            if not user:
                return None

            session = ActiveSession.objects.filter(jti=jti).first()
            if session and session.binding_hash:
                if token.get(JWT_BINDING_CLAIM) != session.binding_hash:
                    return None
                if not session_binding_matches_session(session, scope=scope):
                    return None

            return user, jti
        except (TokenError, KeyError):
            return None
