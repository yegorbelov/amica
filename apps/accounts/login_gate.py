"""Shared device-trust gating after password (or passkey) succeeds."""

from django.utils import timezone

from .device_trust import (
    binding_matches_trusted,
    create_device_challenge,
    notify_trusted_devices,
)
from .models import DeviceRecoveryCooldown, RecoveryEmailOtp
from .recovery_service import (
    create_recovery_otp,
    send_recovery_otp_email,
)


def deferred_login_payload(user, binding_hash: str) -> dict | None:
    """
    If the user should not receive tokens immediately, return a dict for JSON/WS.
    None = proceed with full session issuance.
    """
    if not user.trusted_binding_hash or binding_matches_trusted(user, binding_hash):
        return None

    rc = DeviceRecoveryCooldown.objects.filter(
        user=user, binding_hash=binding_hash
    ).first()
    now = timezone.now()
    if rc:
        if now < rc.cooldown_until:
            return {
                "recovery_cooldown": True,
                "try_after": rc.cooldown_until.isoformat(),
                "message": "Try again after this time to sign in with an email code.",
            }
        existing = (
            RecoveryEmailOtp.objects.filter(
                user=user,
                binding_hash=binding_hash,
                consumed=False,
                expires_at__gt=now,
            )
            .order_by("-created_at")
            .first()
        )
        if existing:
            return {
                "needs_recovery_email_otp": True,
                "otp_id": str(existing.id),
            }
        otp, plain = create_recovery_otp(user, binding_hash)
        send_recovery_otp_email(user, plain)
        return {
            "needs_recovery_email_otp": True,
            "otp_id": str(otp.id),
        }

    challenge, code = create_device_challenge(user, binding_hash)
    notify_trusted_devices(user.id, challenge.id)
    return {
        "needs_device_confirmation": True,
        "challenge_id": str(challenge.id),
        "code": code,
    }
