"""Single trusted device: new bindings require approval on the trusted client."""

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

from .models import DeviceLoginChallenge

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


def binding_matches_trusted(user, binding_hash: str) -> bool:
    if not user.trusted_binding_hash:
        return True
    return binding_hash == user.trusted_binding_hash


def create_device_challenge(user, new_binding_hash: str) -> tuple[DeviceLoginChallenge, str]:
    DeviceLoginChallenge.objects.filter(
        user=user, status=DeviceLoginChallenge.Status.PENDING
    ).update(status=DeviceLoginChallenge.Status.EXPIRED)

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = DeviceLoginChallenge.objects.create(
        user=user,
        new_binding_hash=new_binding_hash,
        code_hash=_hash_code(code),
        expires_at=timezone.now() + CHALLENGE_TTL,
    )
    return challenge, code


def notify_trusted_devices(user_id: int, challenge_id) -> None:
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "device_login_pending",
                "challenge_id": str(challenge_id),
            },
        )
    except Exception as e:
        logger.warning("device_login notify failed for user %s: %s", user_id, e)


def ensure_trusted_from_session_binding(user, session_binding_hash: str | None) -> None:
    if not session_binding_hash or user.trusted_binding_hash:
        return
    from django.db.models import Q

    from .models import CustomUser

    CustomUser.objects.filter(pk=user.pk).filter(
        Q(trusted_binding_hash__isnull=True) | Q(trusted_binding_hash="")
    ).update(trusted_binding_hash=session_binding_hash)
    user.trusted_binding_hash = session_binding_hash
