"""Build transactional email bodies: plain text in code, HTML from templates + CSS file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django.template.loader import render_to_string

_EMAIL_RES = Path(__file__).resolve().parent / "email_resources"


@lru_cache(maxsize=1)
def _email_css() -> str:
    return (_EMAIL_RES / "email.css").read_text(encoding="utf-8")


def _render(template_name: str, context: dict) -> str:
    ctx = {**context, "email_css": _email_css()}
    return render_to_string(f"accounts/email/{template_name}", ctx)


def email_verification(code: str) -> tuple[str, str]:
    text = (
        f"Your Amica email verification code is: {code}\n\n"
        f"It expires in 30 minutes. If you did not register, ignore this message.\n"
    )
    html_body = _render("verify_email.html", {"code": code})
    return text, html_body


def recovery_otp(code: str) -> tuple[str, str]:
    text = (
        f"Your Amica recovery code is: {code}\n\n"
        f"It expires in 15 minutes. If you did not request this, change your password.\n"
    )
    html_body = _render("recovery_otp.html", {"code": code})
    return text, html_body


def recovery_alert(cooldown_display: str) -> tuple[str, str]:
    text = (
        f"Hello,\n\n"
        f"Someone used your password and requested sign-in without your trusted device "
        f"from a new browser profile.\n\n"
        f"If this was not you, change your password immediately.\n\n"
        f"After {cooldown_display} you can complete sign-in from that device "
        f"using a one-time code sent to this email.\n\n"
        f"— Amica"
    )
    html_body = _render(
        "recovery_alert.html", {"cooldown_display": cooldown_display}
    )
    return text, html_body
