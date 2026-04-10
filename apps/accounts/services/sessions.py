from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.Site.tasks.flush_expired_tokens import flush_expired_token

from ..models import ActiveSession


def _notify_session_deleted(user_id: int, jti: str):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"user_{user_id}",
        {
            "type": "session_deleted",
            "session": {"jti": jti},
        },
    )


def revoke_active_session_for_user(user, jti: str, current_jti: str | None) -> str | None:
    """Blacklist refresh, delete row, notify WS. Returns error code or None."""
    try:
        session = ActiveSession.objects.get(user=user, jti=jti)
    except ActiveSession.DoesNotExist:
        return "not_found"
    if current_jti and session.jti == current_jti:
        return "cannot_revoke_current"
    try:
        RefreshToken(session.refresh_token).blacklist()
    except Exception:
        pass
    session.delete()
    _notify_session_deleted(user.id, jti)
    return None


def revoke_other_active_sessions_for_user(user, current_jti: str | None) -> int:
    """Terminate all sessions except current; returns count revoked."""
    qs = ActiveSession.objects.filter(user=user)
    if current_jti:
        qs = qs.exclude(jti=current_jti)
    sessions = list(qs)
    for s in sessions:
        try:
            RefreshToken(s.refresh_token).blacklist()
        except Exception:
            pass
        jti = s.jti
        s.delete()
        _notify_session_deleted(user.id, jti)
    return len(sessions)


def update_user_session_lifetime(user, days, current_refresh_token=None):
    user.preferred_session_lifetime_days = days
    user.save(update_fields=["preferred_session_lifetime_days"])

    current_jti = None
    if current_refresh_token:
        try:
            current_jti = str(RefreshToken(current_refresh_token)["jti"])
        except Exception:
            pass

    sessions = list(ActiveSession.objects.filter(user=user))
    expires_at_by_jti = {}

    for session in sessions:
        if days in [500, 1000, 3000, 6000]:
            expires_at = timezone.now() + timedelta(seconds=days / 100)
        else:
            expires_at = timezone.now() + timedelta(days=days)

        session.expires_at = expires_at
        session.save()

        flush_expired_token.apply_async(args=[session.id], eta=expires_at)

        expires_at_by_jti[session.jti] = session.expires_at.isoformat()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"user_{user.id}",
        {
            "type": "session_lifetime_updated",
            "days": days,
            "expires_at_by_jti": expires_at_by_jti,
        },
    )

    return {
        "days": days,
        "expires_at_by_jti": expires_at_by_jti,
        "current_jti": current_jti,
    }
