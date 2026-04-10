"""Shared device-login gating after password (or Google) succeeds — not passkey."""

from .device_trust import (
    binding_matches_active_session,
    create_device_challenge,
    has_active_sessions,
)
from .models import DeviceLoginChallenge
from .session_payload import trusted_device_minimal_label
from .tasks import (
    deliver_device_login_email_otp,
    deliver_device_login_trusted_notifications,
)


def deferred_login_payload(
    user,
    binding_hash: str,
    *,
    device_challenge_binding_hash: str | None = None,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
) -> dict | None:
    """
    If the user should not receive tokens immediately, return a dict for JSON/WS.
    None = proceed with full session issuance (trusted binding matches an active session).

    - Other active sessions: OTP via WebSocket to trusted clients + security email.
    - No other sessions: 6-digit code emailed to the user.

    The new device gets ``challenge_id`` and submits the code via device_login_submit_code.
    """
    if binding_matches_active_session(user, binding_hash):
        return None

    ch_hash = (
        device_challenge_binding_hash
        if device_challenge_binding_hash is not None
        else binding_hash
    )
    if has_active_sessions(user):
        challenge, _ = create_device_challenge(
            user,
            ch_hash,
            request_ip=request_ip,
            request_user_agent=request_user_agent,
            delivery=DeviceLoginChallenge.Delivery.TRUSTED_DEVICE,
        )
        deliver_device_login_trusted_notifications.delay(str(challenge.id))
        return {
            "needs_device_confirmation": True,
            "challenge_id": str(challenge.id),
            "trusted_device": trusted_device_minimal_label(user),
            "delivery": "trusted_device",
        }

    challenge, _ = create_device_challenge(
        user,
        ch_hash,
        request_ip=request_ip,
        request_user_agent=request_user_agent,
        delivery=DeviceLoginChallenge.Delivery.EMAIL,
    )
    deliver_device_login_email_otp.delay(str(challenge.id))
    return {
        "needs_device_confirmation": True,
        "challenge_id": str(challenge.id),
        "delivery": "email",
    }
