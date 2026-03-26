"""Hard-delete messages soft-deleted for everyone (Message.is_deleted=True)."""

import logging

from celery import shared_task

from apps.Site.models import Message

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


@shared_task
def purge_soft_deleted_messages():
    """
    Physically remove messages marked deleted-for-all. Batched to limit transaction size.
    Does not touch per-user MessageRecipient.is_deleted (delete-for-me).
    """
    total_deleted = 0
    while True:
        batch = list(
            Message.objects.filter(deleted_at__isnull=False).values_list("id", flat=True)[
                :BATCH_SIZE
            ]
        )
        if not batch:
            break
        deleted_count, _ = Message.objects.filter(id__in=batch).delete()
        total_deleted += deleted_count

    msg = f"Hard-deleted {total_deleted} row(s) (including cascades) for soft-deleted messages"
    logger.info(msg)
    return msg
