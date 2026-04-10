"""Device-login challenge helpers and realtime notifications."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from .models import ActiveSession, DeviceLoginChallenge

logger = logging.getLogger(__name__)

CODE_MAX_ATTEMPTS = 5
CHALLENGE_TTL = timedelta(minutes=10)


def _hash_code(code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_challenge_code(challenge: DeviceLoginChallenge, code: str) -> bool:
    normalized = "".join(c for c in (code or "").strip() if c.isdigit())
    if len(normalized) != 6:
        return False
    return hmac.compare_digest(_hash_code(normalized), challenge.code_hash)


def has_active_sessions(user) -> bool:
    return ActiveSession.objects.filter(
        user=user,
        expires_at__gt=timezone.now(),
    ).exists()


def binding_matches_active_session(user, binding_hash: str) -> bool:
    if not binding_hash:
        return False
    return ActiveSession.objects.filter(
        user=user,
        binding_hash=binding_hash,
        expires_at__gt=timezone.now(),
    ).exists()


def create_device_challenge(
    user,
    new_binding_hash: str,
    *,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
    delivery: str = DeviceLoginChallenge.Delivery.TRUSTED_DEVICE,
) -> tuple[DeviceLoginChallenge, str]:
    DeviceLoginChallenge.objects.filter(
        user=user, status=DeviceLoginChallenge.Status.PENDING
    ).update(status=DeviceLoginChallenge.Status.EXPIRED)

    code = f"{secrets.randbelow(1_000_000):06d}"
    ua = (request_user_agent or "").strip()[:2000]
    challenge = DeviceLoginChallenge.objects.create(
        user=user,
        new_binding_hash=new_binding_hash,
        code_hash=_hash_code(code),
        pending_otp=code,
        request_ip=request_ip or None,
        request_user_agent=ua,
        expires_at=timezone.now() + CHALLENGE_TTL,
        delivery=delivery,
    )
    return challenge, code


def notify_trusted_devices(
    user_id: int,
    challenge_id,
    *,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
    request_city: str = "",
    request_country: str = "",
    request_device: str = "",
) -> None:
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "device_login_pending",
                "challenge_id": str(challenge_id),
                "request_ip": request_ip or "",
                "request_user_agent": (request_user_agent or "")[:2000],
                "request_city": request_city or "",
                "request_country": request_country or "",
                "request_device": (request_device or "")[:500],
            },
        )
    except Exception as e:
        logger.warning("device_login notify failed for user %s: %s", user_id, e)


def notify_device_login_status(challenge_id, status: str) -> None:
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        async_to_sync(channel_layer.group_send)(
            f"device_login_{challenge_id}",
            {
                "type": "device_login_status",
                "challenge_id": str(challenge_id),
                "status": status,
            },
        )
    except Exception as e:
        logger.warning(
            "device_login status notify failed for challenge %s: %s",
            challenge_id,
            e,
        )
