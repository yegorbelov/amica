"""Trusted-device recovery OTP + signup email verification OTP."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .email_html import (
    email_verification,
    login_attempt_alert,
    recovery_alert,
    recovery_otp,
)
from .mailout import send_html_email
from .models import DeviceRecoveryCooldown, EmailVerificationOtp, RecoveryEmailOtp

logger = logging.getLogger(__name__)

RECOVERY_OTP_TTL = timedelta(minutes=15)
RECOVERY_COOLDOWN = timedelta(hours=24)
OTP_MAX_ATTEMPTS = 5

EMAIL_VERIFICATION_OTP_TTL = timedelta(minutes=30)
EMAIL_VERIFICATION_OTP_MAX_ATTEMPTS = 5


def _hash_otp(code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_six_digit_against_hash(code_hash: str, code: str) -> bool:
    normalized = "".join(c for c in (code or "").strip() if c.isdigit())
    if len(normalized) != 6:
        return False
    return hmac.compare_digest(_hash_otp(normalized), code_hash)


def verify_otp_code(otp: RecoveryEmailOtp, code: str) -> bool:
    return verify_six_digit_against_hash(otp.code_hash, code)


def create_recovery_otp(user, binding_hash: str) -> tuple[RecoveryEmailOtp, str]:
    RecoveryEmailOtp.objects.filter(
        user=user, binding_hash=binding_hash, consumed=False
    ).update(consumed=True)

    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = RecoveryEmailOtp.objects.create(
        user=user,
        binding_hash=binding_hash,
        code_hash=_hash_otp(code),
        expires_at=timezone.now() + RECOVERY_OTP_TTL,
    )
    return otp, code


def create_email_verification_otp(user) -> tuple[EmailVerificationOtp, str]:
    EmailVerificationOtp.objects.filter(user=user, consumed=False).update(
        consumed=True
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = EmailVerificationOtp.objects.create(
        user=user,
        code_hash=_hash_otp(code),
        expires_at=timezone.now() + EMAIL_VERIFICATION_OTP_TTL,
    )
    return otp, code


def send_email_verification_code_email(user, code: str) -> None:
    text, html_body = email_verification(code)
    send_html_email(user.email, "Confirm your Amica email", text, html_body)


def get_or_create_recovery_cooldown(user, binding_hash: str) -> DeviceRecoveryCooldown:
    rc, created = DeviceRecoveryCooldown.objects.get_or_create(
        user=user,
        binding_hash=binding_hash,
        defaults={
            "cooldown_until": timezone.now() + RECOVERY_COOLDOWN,
        },
    )
    return rc


def send_recovery_alert_email(user, cooldown_until) -> None:
    try:
        cooldown_display = timezone.localtime(cooldown_until).strftime(
            "%Y-%m-%d %H:%M %Z"
        )
    except Exception:
        cooldown_display = cooldown_until.isoformat()
    text, html_body = recovery_alert(cooldown_display)
    send_html_email(
        user.email, "Amica: new device recovery request", text, html_body
    )


def send_recovery_otp_email(user, code: str) -> None:
    text, html_body = recovery_otp(code)
    send_html_email(user.email, "Amica: your recovery code", text, html_body)


def send_device_login_attempt_email(
    user,
    *,
    request_device: str = "",
    request_ip: str = "",
    request_city: str = "",
    request_country: str = "",
) -> None:
    """Notify user by email when a new-device sign-in needs trusted approval."""
    text, html_body = login_attempt_alert(
        request_device=request_device or "",
        request_ip=request_ip or "",
        request_city=request_city or "",
        request_country=request_country or "",
    )
    send_html_email(
        user.email,
        "Amica: sign-in attempt from a new device",
        text,
        html_body,
    )
