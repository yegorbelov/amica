import base64
import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import requests
import uuid

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)

from apps.Site.tasks.flush_expired_tokens import flush_expired_token

from .backup_codes import (
    issue_initial_backup_codes_if_needed,
    regenerate_backup_codes,
    verify_and_consume_backup_code,
)
from .device_trust import (
    CODE_MAX_ATTEMPTS,
    binding_matches_trusted,
    ensure_trusted_from_session_binding,
    verify_challenge_code,
)
from .login_gate import deferred_login_payload
from .models import (
    AccountBackupCode,
    ActiveSession,
    DeviceLoginChallenge,
    EmailVerificationOtp,
)
from .recovery_service import (
    EMAIL_VERIFICATION_OTP_MAX_ATTEMPTS,
    create_email_verification_otp,
    send_email_verification_code_email,
    verify_six_digit_against_hash,
)
from .serializers.serializers import ActiveSessionSerializer, UserSerializer
from .session_binding import (
    JWT_BINDING_CLAIM,
    attach_client_binding_cookie_if_needed,
    binding_from_request,
    binding_from_scope,
    compute_binding_hash,
    enhanced_browser_fingerprint_from_meta,
    poll_binding_matches_device_challenge,
    session_binding_matches_session,
    ua_only_browser_fingerprint_from_meta,
)
from .utils.google_login_or_create_user import google_login_or_create_user

User = get_user_model()

logger = logging.getLogger(__name__)


def create_refresh_token(user, session_lifetime_days):
    token = RefreshToken.for_user(user)
    token.set_exp(
        from_time=timezone.now(), lifetime=timedelta(days=session_lifetime_days)
    )
    return token

def get_client_ip(request):
    ip = request.META.get("HTTP_CF_CONNECTING_IP")
    if ip:
        return ip.strip()

    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
        if ip:
            return ip

    return request.META.get("REMOTE_ADDR")


def remember_session(user, refresh, request, old_jti=None, response=None):
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

    ip = get_client_ip(request)

    session = ActiveSession.objects.create(
        user=user,
        jti=refresh_jti,
        refresh_token=str(refresh),
        ip_address=ip,
        user_agent=request.META.get("HTTP_USER_AGENT"),
        binding_hash=binding_from_request(request, response),
        expires_at=expires_at,
    )

    try:
        flush_expired_token.apply_async(args=[session.id], eta=expires_at)
    except Exception as e:
        logger.warning(
            "Could not schedule flush_expired_token for session %s: %s",
            session.id,
            e,
        )

    return session


def _get_scope_headers(scope):
    """Return dict of lowercased header names to string values."""
    out = {}
    for k, v in scope.get("headers", []):
        if isinstance(k, bytes):
            k = k.decode("latin1")
        if isinstance(v, bytes):
            v = v.decode("latin1")
        out[k.lower()] = v
    return out


def remember_session_from_scope(scope, user, refresh, old_jti=None):
    """Create ActiveSession for WS login/signup. Uses scope headers for IP/user_agent."""
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
    headers = _get_scope_headers(scope)
    ip = (
        headers.get("cf-connecting-ip")
        or (headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or headers.get("x-real-ip")
        or ""
    )
    user_agent = headers.get("user-agent", "")
    session = ActiveSession.objects.create(
        user=user,
        jti=refresh_jti,
        refresh_token=str(refresh),
        ip_address=ip or "ws",
        user_agent=user_agent,
        binding_hash=binding_from_scope(scope),
        expires_at=expires_at,
    )
    try:
        flush_expired_token.apply_async(args=[session.id], eta=expires_at)
    except Exception as e:
        logger.warning(
            "Could not schedule flush_expired_token for session %s: %s",
            session.id,
            e,
        )
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


def ensure_client_binding_cookie(request, response):
    attach_client_binding_cookie_if_needed(request, response)
    return response


@api_view(["POST"])
@permission_classes([AllowAny])
def refresh_token(request):
    token_str = request.COOKIES.get("refresh_token")
    if not token_str:
        return Response({"error": "No refresh token"}, status=401)

    try:
        old_refresh = RefreshToken(token_str)
        jti = str(old_refresh["jti"])
        session = ActiveSession.objects.filter(
            jti=jti, expires_at__gt=timezone.now()
        ).first()
        if not session:
            return Response({"error": "Invalid refresh token"}, status=401)
        if not session_binding_matches_session(session, request=request):
            return Response({"error": "Invalid refresh token"}, status=401)
        user = session.user

        new_refresh = create_refresh_token_for_user(user)

        response = Response({})
        new_session = remember_session(
            user, new_refresh, request, old_jti=jti, response=response
        )
        access = get_access_token_for_session(
            str(new_refresh["jti"]), user, new_session.binding_hash
        )
        response.data["access"] = access
        response = set_refresh_cookie(response, new_refresh)
        return ensure_client_binding_cookie(request, response)

    except Exception:
        return Response({"error": "Invalid refresh token"}, status=401)


def create_refresh_token_for_user(user):
    lifetime_days = getattr(user, "preferred_session_lifetime_days", 7)

    token = RefreshToken.for_user(user)
    token.set_exp(from_time=timezone.now(), lifetime=timedelta(days=lifetime_days))
    return token


def get_new_access_token_for_user(user):
    """Return a new access token string for the user (e.g. for WS refresh without rotation)."""
    return str(create_refresh_token_for_user(user).access_token)


def get_access_token_for_session(session_jti, user, binding_hash=None):
    """Return an access token string for the existing session (same jti). Used for WS connect with refresh cookie."""
    refresh = create_refresh_token_for_user(user)
    refresh.access_token["jti"] = session_jti
    if binding_hash:
        refresh.access_token[JWT_BINDING_CLAIM] = binding_hash
    return str(refresh.access_token)


def _gated_login_response(request, user):
    resp = Response(status=status.HTTP_200_OK)
    dev = attach_client_binding_cookie_if_needed(request, resp)
    binding = compute_binding_hash(
        dev, enhanced_browser_fingerprint_from_meta(request.META)
    )
    challenge_binding = compute_binding_hash(
        dev, ua_only_browser_fingerprint_from_meta(request.META)
    )
    gate = deferred_login_payload(
        user,
        binding,
        device_challenge_binding_hash=challenge_binding,
        request_ip=get_client_ip(request),
        request_user_agent=request.META.get("HTTP_USER_AGENT") or "",
    )
    if not gate:
        return None
    resp.data = gate
    return ensure_client_binding_cookie(request, resp)


def _attach_initial_backup_codes_if_issued(user, response: Response) -> None:
    codes = issue_initial_backup_codes_if_needed(user)
    if codes is not None:
        response.data["backup_codes"] = codes


def _trusted_device_binding_ok(request, user) -> bool:
    if not user.trusted_binding_hash:
        return False
    binding = binding_from_request(request)
    return constant_time_compare(binding, user.trusted_binding_hash)


@api_view(["GET"])
@permission_classes([AllowAny])
def client_binding_bootstrap(request):
    """Ensure binding cookie exists. Do not return the id in JSON (XSS-safe)."""
    response = Response({"ok": True})
    attach_client_binding_cookie_if_needed(request, response)
    return response


@api_view(["POST"])
@permission_classes([AllowAny])
def verify_email_otp(request):
    otp_id = request.data.get("otp_id")
    code = request.data.get("code")
    if not otp_id:
        return Response({"error": "otp_id required"}, status=status.HTTP_400_BAD_REQUEST)
    otp = (
        EmailVerificationOtp.objects.filter(id=otp_id, consumed=False)
        .select_related("user")
        .first()
    )
    if not otp or timezone.now() > otp.expires_at:
        return Response(
            {"error": "invalid or expired otp"}, status=status.HTTP_400_BAD_REQUEST
        )
    user = otp.user
    if user.email_verified_at:
        return Response(
            {"error": "already verified"}, status=status.HTTP_400_BAD_REQUEST
        )
    if otp.attempts >= EMAIL_VERIFICATION_OTP_MAX_ATTEMPTS:
        return Response(
            {"error": "too many attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS
        )
    if not verify_six_digit_against_hash(otp.code_hash, str(code or "")):
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return Response({"error": "invalid code"}, status=status.HTTP_400_BAD_REQUEST)
    otp.consumed = True
    otp.save(update_fields=["consumed"])
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])

    gated = _gated_login_response(request, user)
    if gated is not None:
        return gated

    refresh = create_refresh_token_for_user(user)
    response = Response({"access": None, "user": None})
    session = remember_session(user, refresh, request, response=response)
    ensure_trusted_from_session_binding(user, session.binding_hash)
    access = get_access_token_for_session(
        str(refresh["jti"]), user, session.binding_hash
    )
    response.data["access"] = access
    response.data["user"] = UserSerializer(user, context={"request": request}).data
    _attach_initial_backup_codes_if_issued(user, response)
    response = set_refresh_cookie(response, refresh)
    return ensure_client_binding_cookie(request, response)


@api_view(["POST"])
@permission_classes([AllowAny])
def device_login_submit_code(request):
    """
    New (untrusted) device submits the OTP shown on the trusted device.
    Request binding must match the challenge's new_binding_hash.
    """
    challenge_id = request.data.get("challenge_id")
    code = request.data.get("code")
    if not challenge_id:
        return Response(
            {"error": "challenge_id required"}, status=status.HTTP_400_BAD_REQUEST
        )

    challenge = DeviceLoginChallenge.objects.filter(id=challenge_id).first()
    if not challenge:
        return Response(
            {"error": "Invalid or expired challenge"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if challenge.status == DeviceLoginChallenge.Status.REJECTED:
        return Response(
            {"error": "rejected"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not poll_binding_matches_device_challenge(request, challenge.new_binding_hash):
        return Response({"error": "wrong_client"}, status=status.HTTP_403_FORBIDDEN)

    with transaction.atomic():
        locked = (
            DeviceLoginChallenge.objects.select_for_update()
            .filter(
                id=challenge_id,
                status=DeviceLoginChallenge.Status.PENDING,
            )
            .first()
        )
        if not locked:
            return Response(
                {"error": "Invalid or expired challenge"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if locked.expires_at <= timezone.now():
            locked.status = DeviceLoginChallenge.Status.EXPIRED
            locked.save(update_fields=["status"])
            return Response({"error": "Challenge expired"}, status=status.HTTP_410_GONE)
        if locked.attempts >= CODE_MAX_ATTEMPTS:
            return Response(
                {"error": "Too many attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        if not verify_challenge_code(locked, str(code or "")):
            locked.attempts += 1
            locked.save(update_fields=["attempts"])
            return Response({"error": "Invalid code"}, status=status.HTTP_400_BAD_REQUEST)
        locked.status = DeviceLoginChallenge.Status.APPROVED
        locked.pending_otp = ""
        locked.save(update_fields=["status", "pending_otp"])

    return Response({"success": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def device_login_trusted_decision(request):
    """
    Trusted session only: allow (returns OTP) or deny new-device login challenge.
    """
    user = request.user
    if not _trusted_device_binding_ok(request, user):
        return Response({"error": "forbidden"}, status=status.HTTP_403_FORBIDDEN)

    challenge_id = request.data.get("challenge_id")
    decision = (request.data.get("decision") or "").strip().lower()
    if not challenge_id:
        return Response(
            {"error": "challenge_id required"}, status=status.HTTP_400_BAD_REQUEST
        )

    challenge = DeviceLoginChallenge.objects.filter(
        id=challenge_id, user=user
    ).first()
    if not challenge:
        return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)

    now = timezone.now()
    if challenge.status != DeviceLoginChallenge.Status.PENDING:
        return Response(
            {"error": "invalid challenge"}, status=status.HTTP_400_BAD_REQUEST
        )
    if challenge.expires_at <= now:
        challenge.status = DeviceLoginChallenge.Status.EXPIRED
        challenge.pending_otp = ""
        challenge.save(update_fields=["status", "pending_otp"])
        return Response({"error": "expired"}, status=status.HTTP_410_GONE)

    if decision == "deny":
        challenge.status = DeviceLoginChallenge.Status.REJECTED
        challenge.pending_otp = ""
        challenge.save(update_fields=["status", "pending_otp"])
        return Response({"ok": True})

    if decision == "allow":
        otp = (challenge.pending_otp or "").strip()
        if len(otp) != 6 or not otp.isdigit():
            return Response({"error": "no code"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"code": otp})

    return Response({"error": "decision required"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([AllowAny])
def device_login_poll(request, challenge_id):
    # Do not mint a new binding cookie on 403 — that rotates the device id and breaks Safari/clients
    # that already have a cookie from the deferred-login response.
    challenge = DeviceLoginChallenge.objects.filter(id=challenge_id).first()
    if not challenge:
        return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)

    if not poll_binding_matches_device_challenge(request, challenge.new_binding_hash):
        return Response({"error": "wrong_client"}, status=status.HTTP_403_FORBIDDEN)

    if timezone.now() > challenge.expires_at:
        if challenge.status == DeviceLoginChallenge.Status.PENDING:
            challenge.status = DeviceLoginChallenge.Status.EXPIRED
            challenge.save(update_fields=["status"])
        return Response({"status": "expired"}, status=status.HTTP_410_GONE)

    if challenge.status == DeviceLoginChallenge.Status.PENDING:
        return Response({"status": "pending"})

    if challenge.status == DeviceLoginChallenge.Status.REJECTED:
        return Response({"status": "rejected"})

    if challenge.status != DeviceLoginChallenge.Status.APPROVED:
        return Response({"status": "expired"}, status=status.HTTP_410_GONE)

    with transaction.atomic():
        locked = (
            DeviceLoginChallenge.objects.select_for_update()
            .filter(
                id=challenge_id,
                status=DeviceLoginChallenge.Status.APPROVED,
            )
            .first()
        )
        if not locked:
            return Response({"status": "pending"})
        user_id = locked.user_id
        locked.delete()

    user = User.objects.get(pk=user_id)
    refresh = create_refresh_token_for_user(user)
    response = Response(
        {
            "status": "ok",
            "access": None,
            "user": None,
        }
    )
    session = remember_session(user, refresh, request, response=response)
    ensure_trusted_from_session_binding(user, session.binding_hash)
    access = get_access_token_for_session(
        str(refresh["jti"]), user, session.binding_hash
    )
    response.data["access"] = access
    response.data["user"] = UserSerializer(user, context={"request": request}).data
    _attach_initial_backup_codes_if_issued(user, response)
    response = set_refresh_cookie(response, refresh)
    return ensure_client_binding_cookie(request, response)


@api_view(["POST"])
@permission_classes([AllowAny])
def api_login(request):
    identifier = request.data.get("email") or request.data.get("username")
    password = request.data.get("password")
    backup_code = (request.data.get("backup_code") or "").strip()
    user = authenticate(username=identifier, password=password)
    if not user:
        return Response({"error": "Invalid credentials"}, status=400)

    if not user.email_verified_at:
        return Response(
            {
                "error": "email_not_verified",
                "email": user.email,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    response = Response({"access": None, "user": None})
    dev = attach_client_binding_cookie_if_needed(request, response)
    binding = compute_binding_hash(
        dev, enhanced_browser_fingerprint_from_meta(request.META)
    )

    if not binding_matches_trusted(user, binding):
        if backup_code:
            if not verify_and_consume_backup_code(user, backup_code):
                err_resp = Response(
                    {"error": "invalid_backup_code"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
                attach_client_binding_cookie_if_needed(request, err_resp)
                return err_resp
            User.objects.filter(pk=user.pk).update(trusted_binding_hash=binding)
            user.trusted_binding_hash = binding
        else:
            challenge_binding = compute_binding_hash(
                dev, ua_only_browser_fingerprint_from_meta(request.META)
            )
            gate = deferred_login_payload(
                user,
                binding,
                device_challenge_binding_hash=challenge_binding,
                request_ip=get_client_ip(request),
                request_user_agent=request.META.get("HTTP_USER_AGENT") or "",
            )
            if gate:
                response.data = gate
                return ensure_client_binding_cookie(request, response)

    refresh = create_refresh_token_for_user(user)
    session = remember_session(user, refresh, request, response=response)
    ensure_trusted_from_session_binding(user, session.binding_hash)
    access = get_access_token_for_session(
        str(refresh["jti"]), user, session.binding_hash
    )

    response.data["access"] = access
    response.data["user"] = UserSerializer(user, context={"request": request}).data
    _attach_initial_backup_codes_if_issued(user, response)
    response = set_refresh_cookie(response, refresh)
    return ensure_client_binding_cookie(request, response)


@api_view(["POST"])
@permission_classes([AllowAny])
def google_login(request):
    access_token = request.data.get("access_token")
    if not access_token:
        return Response(
            {"error": "No access token provided"}, status=status.HTTP_400_BAD_REQUEST
        )

    token_info = requests.get(
        f"https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={access_token}"
    ).json()
    if "error" in token_info:
        return Response({"error": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)

    email = token_info.get("email")
    if not email:
        return Response(
            {"error": "Email not found in token"}, status=status.HTTP_400_BAD_REQUEST
        )

    user = google_login_or_create_user(request, access_token)

    if not user.email_verified_at:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])

    gated = _gated_login_response(request, user)
    if gated is not None:
        return gated

    refresh = RefreshToken.for_user(user)
    serializer = UserSerializer(user, context={"request": request})
    response = Response({"access": None, "user": None})
    session = remember_session(user, refresh, request, response=response)
    ensure_trusted_from_session_binding(user, session.binding_hash)
    access = get_access_token_for_session(
        str(refresh["jti"]), user, session.binding_hash
    )
    response.data["access"] = access
    response.data["user"] = serializer.data
    _attach_initial_backup_codes_if_issued(user, response)
    response = set_refresh_cookie(response, refresh)
    return ensure_client_binding_cookie(request, response)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def backup_codes_status(request):
    if not _trusted_device_binding_ok(request, request.user):
        return Response(
            {"error": "trusted_device_required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    n = AccountBackupCode.objects.filter(
        user=request.user, used_at__isnull=True
    ).count()
    return Response({"unused_count": n})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def backup_codes_regenerate(request):
    if not _trusted_device_binding_ok(request, request.user):
        return Response(
            {"error": "trusted_device_required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    codes = regenerate_backup_codes(request.user)
    response = Response({"backup_codes": codes})
    return ensure_client_binding_cookie(request, response)


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

    ev_otp, plain = create_email_verification_otp(user)
    try:
        send_email_verification_code_email(user, plain)
    except Exception:
        logger.exception("verification email failed for user %s", user.pk)

    response = Response(
        {
            "needs_email_verification": True,
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "email_verification_otp_id": str(ev_otp.id),
        },
        status=status.HTTP_201_CREATED,
    )
    return ensure_client_binding_cookie(request, response)


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

        if not user.email_verified_at:
            return Response(
                {"error": "email_not_verified"},
                status=status.HTTP_403_FORBIDDEN,
            )

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

        gated = _gated_login_response(request, user)
        if gated is not None:
            del request.session["passkey_challenge"]
            del request.session["passkey_user_email"]
            return gated

        refresh = RefreshToken.for_user(user)
        serializer = UserSerializer(user, context={"request": request})
        response = Response(
            {"success": True, "message": "Passkey registered", **serializer.data}
        )
        session = remember_session(user, refresh, request, response=response)
        ensure_trusted_from_session_binding(user, session.binding_hash)
        _attach_initial_backup_codes_if_issued(user, response)
        response = set_refresh_cookie(response, refresh)

        del request.session["passkey_challenge"]
        del request.session["passkey_user_email"]

        return ensure_client_binding_cookie(request, response)

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

        if not user.email_verified_at:
            del request.session["passkey_challenge"]
            return Response(
                {"error": "email_not_verified"},
                status=status.HTTP_403_FORBIDDEN,
            )

        gated = _gated_login_response(request, user)
        if gated is not None:
            del request.session["passkey_challenge"]
            return gated

        refresh = RefreshToken.for_user(user)
        serializer = UserSerializer(user, context={"request": request})
        response = Response(
            {
                "success": True,
                "message": "Passkey login successful!",
                "access": None,
                "user": serializer.data,
            }
        )
        session = remember_session(user, refresh, request, response=response)
        ensure_trusted_from_session_binding(user, session.binding_hash)
        access = get_access_token_for_session(
            str(refresh["jti"]), user, session.binding_hash
        )
        response.data["access"] = access
        _attach_initial_backup_codes_if_issued(user, response)
        response = set_refresh_cookie(response, refresh)

        del request.session["passkey_challenge"]
        return ensure_client_binding_cookie(request, response)

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
