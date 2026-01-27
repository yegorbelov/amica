from datetime import timedelta
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from apps.accounts.models import ActiveSession
import logging

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

        refresh_token_str = request.COOKIES.get("refresh_token")
        if refresh_token_str:
            try:
                jti = RefreshToken(refresh_token_str).payload.get("jti")
                if jti:
                    session, _ = ActiveSession.objects.get_or_create(user=user, jti=jti)
                    now = timezone.now()
                    if now - session.last_active > self.SESSION_UPDATE_INTERVAL:
                        ActiveSession.objects.filter(pk=session.pk).update(last_active=now)
            except TokenError:
                pass

        return result
