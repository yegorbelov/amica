import random
import logging
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.Site.models import Chat

logger = logging.getLogger(__name__)


def run():
    User = get_user_model()
    users = list(User.objects.all())

    if len(users) < 2:
        logger.error("Not enough users.")
        return

    created_count = 0

    for i, user1 in enumerate(users):
        for user2 in users[i + 1 :]:
            if random.random() < 0.3:
                try:
                    with transaction.atomic():
                        chat, created = Chat.get_or_create_direct_chat(user1, user2)
                        if created:
                            created_count += 1
                            logger.info(
                                f"Dialog created: {user1.email} ↔ {user2.email} (Chat ID: {chat.id})"
                            )
                except Exception as e:
                    logger.error(f"Error creating dialog {user1.email} ↔ {user2.email}: {e}")

    logger.info(f"Total dialogs created: {created_count}")
