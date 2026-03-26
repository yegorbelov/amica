"""Orphan media cleanup when messages are removed."""

import logging

logger = logging.getLogger(__name__)


def delete_orphan_message_files(message) -> None:
    """
    For each file attached to this message, delete the File row (and files on
    storage via File.delete()) if no other message references it.
    """
    from apps.Site.models import Message

    files = list(message.file.all())
    if not files:
        return

    for f in files:
        qs = Message.objects.filter(file=f)
        if message.pk is not None:
            qs = qs.exclude(pk=message.pk)
        if qs.exists():
            continue
        try:
            pk = f.pk
            f.delete()
            logger.info("Deleted orphan media_files.File id=%s (no other messages)", pk)
        except Exception:
            logger.exception(
                "Failed to delete orphan file id=%s for message id=%s",
                getattr(f, "pk", None),
                getattr(message, "pk", None),
            )
            raise
