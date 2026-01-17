# ws/services/session_service.py
import logging
from typing import Optional

from asgiref.sync import sync_to_async

from apps.accounts.services.sessions import update_user_session_lifetime

from ..exceptions import WSValidationError
from ..repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)


class SessionService:
    @staticmethod
    async def is_active(jti: Optional[str]) -> bool:
        if not jti:
            return False

        try:
            return await SessionRepository.is_active(jti)
        except Exception as e:
            logger.error(f"Error checking session activity for jti={jti}: {e}")
            return False

    @staticmethod
    async def update_lifetime(
        user, days: int, refresh_token: Optional[str] = None
    ) -> bool:
        if not user.is_authenticated:
            logger.warning(f"Attempt to update session lifetime for anonymous user")
            raise WSValidationError("User is not authenticated")

        if not isinstance(days, int) or days <= 0:
            raise WSValidationError("Days must be a positive integer")

        if not refresh_token:
            logger.warning("Update session lifetime called without refresh_token")
            raise WSValidationError("Refresh token is required")

        try:
            result = await sync_to_async(update_user_session_lifetime)(
                user, days, current_refresh_token=refresh_token
            )

            if result:
                logger.info(
                    f"Session lifetime updated for user {user.username} to {days} days"
                )
            else:
                logger.warning(
                    f"Failed to update session lifetime for user {user.username}"
                )

            return bool(result)

        except Exception as e:
            logger.error(
                f"Exception during session lifetime update for user {user.id}: {e}"
            )
            raise WSValidationError("Failed to update session lifetime")
