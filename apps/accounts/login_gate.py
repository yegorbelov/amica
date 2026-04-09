"""Shared device-trust gating after password (or passkey) succeeds."""

from .device_trust import binding_matches_trusted, create_device_challenge
from .session_payload import trusted_device_minimal_label
from .tasks import deliver_device_login_trusted_notifications


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
    challenge, _ = create_device_challenge(
        user,
        ch_hash,
        request_ip=request_ip,
        request_user_agent=request_user_agent,
    )
    deliver_device_login_trusted_notifications.delay(str(challenge.id))
    return {
        "needs_device_confirmation": True,
        "challenge_id": str(challenge.id),
        "trusted_device": trusted_device_minimal_label(user),
    }
