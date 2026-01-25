from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from apps.accounts.models import ActiveSession


from rest_framework_simplejwt.tokens import AccessToken, TokenError

class BearerJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            user, token = result
        else:
            access_token_str = request.COOKIES.get("access_token")
            if not access_token_str:
                return None
            try:
                validated_token = AccessToken(access_token_str)
                user = self.get_user(validated_token)
                token = validated_token
            except TokenError:
                raise AuthenticationFailed("Invalid access token in cookie")

        refresh_token_str = request.COOKIES.get("refresh_token")
        if refresh_token_str:
            try:
                jti = RefreshToken(refresh_token_str).payload.get("jti")
                if jti:
                    session = ActiveSession.objects.get(user=user, jti=jti)
                    now = timezone.now()
                    if now - session.last_active > timedelta(seconds=10):
                        ActiveSession.objects.filter(pk=session.pk).update(last_active=now)
            except (TokenError, ActiveSession.DoesNotExist):
                pass

        return (user, token)

