import base64
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)

from apps.Site.tasks.flush_expired_tokens import flush_expired_token

from .models import ActiveSession
from .serializers.serializers import ActiveSessionSerializer, UserSerializer
from .utils.google_login_or_create_user import google_login_or_create_user

User = get_user_model()


def create_refresh_token(user, session_lifetime_days):
    token = RefreshToken.for_user(user)
    token.set_exp(
        from_time=timezone.now(), lifetime=timedelta(days=session_lifetime_days)
    )
    return token


def remember_session(user, refresh, request, old_jti=None):
    refresh_jti = str(refresh["jti"])
    lifetime_days = getattr(user, "preferred_session_lifetime_days", 7)

    if (
        lifetime_days == 500
        or lifetime_days == 1000
        or lifetime_days == 3000
        or lifetime_days == 6000
    ):
        expires_at = timezone.now() + timedelta(seconds=lifetime_days / 100)
    else:
        expires_at = timezone.now() + timedelta(days=lifetime_days)

    if old_jti:
            ActiveSession.objects.filter(jti=old_jti).delete()
    
    session = ActiveSession.objects.create(
        user=user,
        jti=refresh_jti,
        refresh_token=str(refresh),
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT"),
        expires_at=expires_at,
    )

    flush_expired_token.apply_async(args=[session.id], eta=expires_at)
    return session


from datetime import datetime
from datetime import timezone as dt_timezone


def set_refresh_cookie(response, refresh: RefreshToken):
    lifetime_seconds = refresh["exp"] - int(
        datetime.now(tz=dt_timezone.utc).timestamp()
    )
    response.set_cookie(
        "refresh_token",
        str(refresh),
        httponly=True,
        secure=False,
        samesite="Lax",
        max_age=lifetime_seconds,
        path="/",
    )
    return response


@api_view(["POST"])
@permission_classes([AllowAny])
def refresh_token(request):
    token_str = request.COOKIES.get("refresh_token")
    if not token_str:
        return Response({"error": "No refresh token"}, status=401)

    try:
        old_refresh = RefreshToken(token_str)
        user = ActiveSession.objects.get(jti=str(old_refresh["jti"])).user

        new_refresh = create_refresh_token_for_user(user)

        remember_session(user, new_refresh, request, old_jti=str(old_refresh["jti"]))

        response = Response({"access": str(new_refresh.access_token)})
        return set_refresh_cookie(response, new_refresh)

    except Exception:
        return Response({"error": "Invalid refresh token"}, status=401)



def create_refresh_token_for_user(user):
    lifetime_days = getattr(user, "preferred_session_lifetime_days", 7)

    token = RefreshToken.for_user(user)
    token.set_exp(from_time=timezone.now(), lifetime=timedelta(days=lifetime_days))
    return token


@api_view(["POST"])
@permission_classes([AllowAny])
def api_login(request):
    user = authenticate(
        username=request.data.get("username"), password=request.data.get("password")
    )
    if not user:
        return Response({"error": "Invalid credentials"}, status=400)

    refresh = create_refresh_token_for_user(user)
    access = str(refresh.access_token)

    remember_session(user, refresh, request)

    response = Response(
        {
            "access": access,
            "user": UserSerializer(user, context={"request": request}).data,
        }
    )
    return set_refresh_cookie(response, refresh)


@api_view(["POST"])
@permission_classes([AllowAny])
def google_login(request):
    token = request.data.get("id_token")
    if not token:
        return Response(
            {"error": "No token provided"}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_ID
        user = google_login_or_create_user(request, token, GOOGLE_CLIENT_ID)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    refresh = RefreshToken.for_user(user)
    remember_session(user, refresh, request)
    serializer = UserSerializer(user, context={"request": request})

    response = Response({"access": str(refresh.access_token), "user": serializer.data})
    response = set_refresh_cookie(response, refresh)

    return response


@api_view(["POST"])
def logout(request):
    refresh = request.COOKIES.get("refresh_token")
    if refresh:
        ActiveSession.objects.filter(jti=str(RefreshToken(refresh)["jti"])).delete()
    response = Response(status=204)
    response.delete_cookie("refresh_token")
    return response


@api_view(["POST"])
@permission_classes([AllowAny])
def signup(request):
    username = request.data.get("username")
    email = request.data.get("email")
    password = request.data.get("password")

    if not email or not password:
        return Response(
            {"error": "email and password are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.create_user(
            email=email,
            password=password,
            username=username,
        )
    except IntegrityError:
        return Response(
            {"error": "User already exists"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    refresh = RefreshToken.for_user(user)
    remember_session(user, refresh, request)
    response = Response(
        {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
        },
        status=status.HTTP_201_CREATED,
    )

    response = set_refresh_cookie(response, refresh)

    return response


def base64url_to_bytes(val: str) -> bytes:
    padding = "=" * ((4 - len(val) % 4) % 4)
    return base64.urlsafe_b64decode(val + padding)


@api_view(["POST"])
@permission_classes([AllowAny])
def passkey_register_start(request):
    email = request.data.get("email")
    if not email:
        return Response({"error": "Email required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email=email)
        user_id = str(user.id).encode()
    except User.DoesNotExist:
        return Response(
            {"error": "User not found. Signup first"}, status=status.HTTP_404_NOT_FOUND
        )

    options = generate_registration_options(
        rp_id=request.get_host().split(":")[0],
        rp_name="Amica",
        user_id=user_id,
        user_name=email,
        user_display_name=user.display_name,
        authenticator_selection=None,
        attestation="none",
    )

    request.session["passkey_challenge"] = (
        base64.urlsafe_b64encode(options.challenge).decode().rstrip("=")
    )
    request.session["passkey_user_email"] = email

    response_data = asdict(options)
    response_data["pubKeyCredParams"] = [
        {"type": "public-key", "alg": -7},
        {"type": "public-key", "alg": -257},
    ]
    response_data["challenge"] = request.session["passkey_challenge"]
    response_data["user"]["id"] = (
        base64.urlsafe_b64encode(options.user.id).decode().rstrip("=")
    )

    response_data["user"]["displayName"] = user.display_name

    return Response(response_data)


@api_view(["POST"])
@permission_classes([AllowAny])
def passkey_register_finish(request):
    try:
        body = json.loads(request.body)

        challenge_b64 = request.session.get("passkey_challenge")
        email = request.session.get("passkey_user_email")

        if not challenge_b64 or not email:
            return Response(
                {"error": "Session expired"}, status=status.HTTP_400_BAD_REQUEST
            )

        user = User.objects.get(email=email)

        expected_challenge = base64url_to_bytes(challenge_b64)

        credential = {
            "id": body["id"],
            "rawId": body["rawId"],
            "type": body["type"],
            "response": {
                "clientDataJSON": body["response"]["clientDataJSON"],
                "attestationObject": body["response"]["attestationObject"],
            },
        }
        webauthn_resp = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            expected_rp_id=request.get_host().split(":")[0],
        )
        user.credential_id = webauthn_resp.credential_id
        user.credential_public_key = webauthn_resp.credential_public_key
        user.sign_count = webauthn_resp.sign_count
        user.save()

        refresh = RefreshToken.for_user(user)
        remember_session(user, refresh, request)
        serializer = UserSerializer(user, context={"request": request})
        response = Response(
            {"success": True, "message": "Passkey registered", **serializer.data}
        )
        response = set_refresh_cookie(response, refresh)

        del request.session["passkey_challenge"]
        del request.session["passkey_user_email"]

        return response

    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([AllowAny])
def passkey_auth_start(request):
    users_with_passkeys = User.objects.filter(credential_id__isnull=False)

    credentials = []
    for user in users_with_passkeys:
        cred_id_b64 = base64.urlsafe_b64encode(user.credential_id).decode().rstrip("=")
        credentials.append({"type": "public-key", "id": cred_id_b64})

    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=credentials,
        user_verification="preferred",
    )

    request.session["passkey_challenge"] = (
        base64.urlsafe_b64encode(options.challenge).decode().rstrip("=")
    )

    response_data = {
        "challenge": base64.urlsafe_b64encode(options.challenge).decode().rstrip("="),
        "rpId": options.rp_id,
        "allowCredentials": credentials,
        "timeout": getattr(options, "timeout", 60000),
        "userVerification": getattr(options, "user_verification", "preferred"),
    }

    return Response(response_data)


@api_view(["POST"])
@permission_classes([AllowAny])
def passkey_auth_finish(request):
    try:
        body = json.loads(request.body)
        challenge_b64 = request.session.get("passkey_challenge")

        if not challenge_b64:
            return Response(
                {"error": "Session expired"}, status=status.HTTP_400_BAD_REQUEST
            )

        expected_challenge = base64url_to_bytes(challenge_b64)

        credential_id = base64url_to_bytes(body["rawId"])
        try:
            user = User.objects.get(credential_id=credential_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Passkey not found"}, status=status.HTTP_404_NOT_FOUND
            )

        credential = {
            "id": body["id"],
            "rawId": body["rawId"],
            "type": body["type"],
            "response": {
                "clientDataJSON": body["response"]["clientDataJSON"],
                "authenticatorData": body["response"]["authenticatorData"],
                "signature": body["response"]["signature"],
                "userHandle": body["response"].get("userHandle"),
            },
        }

        webauthn_resp = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            credential_public_key=user.credential_public_key,
            credential_current_sign_count=user.sign_count,
        )

        user.sign_count = webauthn_resp.new_sign_count
        user.save()

        refresh = RefreshToken.for_user(user)
        remember_session(user, refresh, request)
        response = Response({"success": True, "message": "Passkey login successful!"})
        response = set_refresh_cookie(response, refresh)

        del request.session["passkey_challenge"]
        return response

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ActiveSessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sessions = ActiveSession.objects.filter(user=request.user)
        serializer = ActiveSessionSerializer(
            sessions, many=True, context={"request": request}
        )
        return Response(serializer.data)

    def delete(self, request, jti):
        session = get_object_or_404(
            ActiveSession,
            user=request.user,
            jti=jti,
        )

        try:
            RefreshToken(session.refresh_token).blacklist()
        except Exception:
            pass

        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class KillOtherSessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        token = request.COOKIES.get("refresh_token")
        if not token:
            return Response(
                {"error": "No refresh token"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            current_refresh = RefreshToken(token)
        except Exception:
            return Response(
                {"error": "Invalid refresh token"}, status=status.HTTP_400_BAD_REQUEST
            )

        current_session = ActiveSession.objects.filter(
            user=request.user, refresh_token=str(current_refresh)
        ).first()
        current_jti = current_session.jti if current_session else None

        sessions = ActiveSession.objects.filter(user=request.user)
        if current_jti:
            sessions = sessions.exclude(jti=current_jti)

        deleted_count = sessions.count()

        for s in sessions:
            try:
                RefreshToken(s.refresh_token).blacklist()
            except Exception:
                pass

        sessions.delete()
        return Response(
            {"detail": f"Terminated {deleted_count} session(s)"},
            status=status.HTTP_200_OK,
        )