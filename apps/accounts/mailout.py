import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail

logger = logging.getLogger(__name__)


def _from_email() -> str:
    return getattr(
        settings,
        "DEFAULT_FROM_EMAIL",
        getattr(settings, "SERVER_EMAIL", "webmaster@localhost"),
    )


def send_plain(to_email: str, subject: str, body: str) -> None:
    try:
        send_mail(
            subject,
            body,
            _from_email(),
            [to_email],
            fail_silently=False,
        )
    except Exception as e:
        logger.exception("send_mail failed to %s: %s", to_email, e)
        raise


def send_html_email(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> None:
    """Send multipart/alternative: plain text + HTML."""
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=_from_email(),
            to=[to_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
    except Exception as e:
        logger.exception("send_html_email failed to %s: %s", to_email, e)
        raise
