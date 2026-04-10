import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def deliver_device_login_email_otp(challenge_id: str) -> None:
    """Email the 6-digit code when no other active sessions exist (new-device login)."""
    from .models import DeviceLoginChallenge
    from .recovery_service import send_device_login_email_otp as send_email_otp

    challenge = (
        DeviceLoginChallenge.objects.select_related("user")
        .filter(id=challenge_id)
        .first()
    )
    if not challenge:
        logger.info("device_login email otp skipped: challenge %s missing", challenge_id)
        return
    if challenge.status != DeviceLoginChallenge.Status.PENDING:
        logger.info(
            "device_login email otp skipped: challenge %s status=%s",
            challenge_id,
            challenge.status,
        )
        return
    code = (challenge.pending_otp or "").strip()
    if len(code) != 6 or not code.isdigit():
        logger.warning("device_login email otp skipped: challenge %s bad code", challenge_id)
        return
    try:
        send_email_otp(challenge.user, code)
    except Exception:
        logger.exception(
            "device login email OTP failed for user %s",
            challenge.user_id,
        )


@shared_task
def deliver_device_login_trusted_notifications(challenge_id: str) -> None:
    """
    GeoIP, WebSocket push to trusted clients, and security email — off the login HTTP path.
    """
    from .device_trust import notify_trusted_devices
    from .models import DeviceLoginChallenge
    from .recovery_service import send_device_login_attempt_email
    from .session_payload import device_login_notify_extras

    challenge = (
        DeviceLoginChallenge.objects.select_related("user")
        .filter(id=challenge_id)
        .first()
    )
    if not challenge:
        logger.info("device_login notify skipped: challenge %s missing", challenge_id)
        return
    if challenge.status != DeviceLoginChallenge.Status.PENDING:
        logger.info(
            "device_login notify skipped: challenge %s status=%s",
            challenge_id,
            challenge.status,
        )
        return

    ip = str(challenge.request_ip) if challenge.request_ip else ""
    ua = challenge.request_user_agent or ""
    extras = device_login_notify_extras(ip or None, ua, include_versions=True)
    notify_trusted_devices(
        challenge.user_id,
        challenge.id,
        request_ip=ip,
        request_user_agent=ua,
        request_city=extras["request_city"],
        request_country=extras["request_country"],
        request_device=extras["request_device"],
    )
    try:
        send_device_login_attempt_email(
            challenge.user,
            request_device=extras["request_device"],
            request_ip=ip,
            request_city=extras["request_city"],
            request_country=extras["request_country"],
        )
    except Exception:
        logger.exception(
            "device login attempt email failed for user %s",
            challenge.user_id,
        )
