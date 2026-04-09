"""Shared device-trust gating after password (or passkey) succeeds."""

import logging

from .device_trust import (
    binding_matches_trusted,
    create_device_challenge,
    notify_trusted_devices,
)
from .recovery_service import send_device_login_attempt_email
from .session_payload import device_login_notify_extras, trusted_device_minimal_label

logger = logging.getLogger(__name__)


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
    None = proceed with full session issuance.

    OTP is delivered to trusted sessions via WebSocket; the new device gets
    ``challenge_id``, optional ``trusted_device`` (minimal label for the trusted
    session), and must submit the code via device_login_submit_code.
    """
    if not user.trusted_binding_hash or binding_matches_trusted(user, binding_hash):
        return None

    ch_hash = (
        device_challenge_binding_hash
        if device_challenge_binding_hash is not None
        else binding_hash
    )
    challenge, code = create_device_challenge(
        user,
        ch_hash,
        request_ip=request_ip,
        request_user_agent=request_user_agent,
    )
    extras = device_login_notify_extras(
        request_ip, request_user_agent, include_versions=True
    )
    notify_trusted_devices(
        user.id,
        challenge.id,
        request_ip=request_ip,
        request_user_agent=request_user_agent,
        request_city=extras["request_city"],
        request_country=extras["request_country"],
        request_device=extras["request_device"],
    )
    try:
        send_device_login_attempt_email(
            user,
            request_device=extras["request_device"],
            request_ip=request_ip or "",
            request_city=extras["request_city"],
            request_country=extras["request_country"],
        )
    except Exception:
        logger.exception(
            "device login attempt email failed for user %s",
            getattr(user, "pk", user),
        )
    return {
        "needs_device_confirmation": True,
        "challenge_id": str(challenge.id),
        "trusted_device": trusted_device_minimal_label(user),
    }
