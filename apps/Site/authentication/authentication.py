from datetime import timedelta
from django.utils import timezone
import logging

from apps.accounts.models import ActiveSession
from apps.accounts.session_binding import (
    JWT_BINDING_CLAIM,
    session_binding_matches_session,
)
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

logger = logging.getLogger(__name__)


class BearerJWTAuthentication(JWTAuthentication):
    SESSION_UPDATE_INTERVAL = timedelta(seconds=10)

    def get_header(self, request):
        token = request.query_params.get("token")
        if token:
            logger.info(f"JWT token from URL: {token}")
            return f"Bearer {token}".encode()
        return super().get_header(request)

    def authenticate(self, request):
        result = super().authenticate(request)
        if not result:
            logger.info("No valid JWT token found")
            return None

        user, token = result

        jti = token.get("jti")
        if jti:
            session = ActiveSession.objects.filter(jti=jti).first()
            if session and session.binding_hash:
                if token.get(JWT_BINDING_CLAIM) != session.binding_hash:
                    return None
                if not session_binding_matches_session(session, request=request):
                    return None

        profile = getattr(user, "profile", None)
        if profile:
            profile.update_last_seen()

        refresh_token_str = request.COOKIES.get("refresh_token")
        if refresh_token_str:
            try:
                jti = RefreshToken(refresh_token_str).payload.get("jti")
                if jti:
                    session, _ = ActiveSession.objects.get_or_create(user=user, jti=jti)
                    now = timezone.now()
                    if now - session.last_active > self.SESSION_UPDATE_INTERVAL:
                        ActiveSession.objects.filter(pk=session.pk).update(
                            last_active=now
                        )
            except TokenError:
                pass

        return result
