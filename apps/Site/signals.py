import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import receiver

from apps.media_files.models.models import DisplayVideo, VideoFile

from .models import Chat, Message, MessageRecipient
from .tasks.compress_video_task import compress_video_task
from .utils.message_media import delete_orphan_message_files


@receiver(pre_delete, sender=Message)
def remove_orphan_files_before_message_delete(sender, instance, **kwargs):
    delete_orphan_message_files(instance)


@receiver(post_save, sender=Message)
def create_message_recipients(sender, instance, created, **kwargs):
    if created:
        chat_users = instance.chat.users.all()
        recipients = [
            MessageRecipient(message=instance, user=user) for user in chat_users
        ]
        MessageRecipient.objects.bulk_create(recipients)


User = get_user_model()


@receiver(m2m_changed, sender=Chat.users.through)
def add_existing_messages_to_new_user(sender, instance, action, pk_set, **kwargs):
    if action != "post_add":
        return

    new_users = list(User.objects.filter(pk__in=pk_set).only("id"))
    if not new_users:
        return

    message_ids = list(instance.messages.values_list("id", flat=True))

    if not message_ids:
        return

    existing_links = set(
        MessageRecipient.objects.filter(
            user_id__in=pk_set,
            message_id__in=message_ids,
        ).values_list("message_id", "user_id")
    )

    recipients_to_create = []

    for user in new_users:
        for message_id in message_ids:
            link = (message_id, user.id)
            if link not in existing_links:
                recipients_to_create.append(
                    MessageRecipient(message_id=message_id, user_id=user.id)
                )

    if recipients_to_create:
        MessageRecipient.objects.bulk_create(recipients_to_create, batch_size=500)


@receiver(m2m_changed, sender=Chat.users.through)
def remove_recipients_when_user_leaves_chat(sender, instance, action, pk_set, **kwargs):
    if action != "post_remove":
        return

    user_ids = list(pk_set)

    MessageRecipient.objects.filter(
        user_id__in=user_ids, message__chat=instance
    ).delete()



logger = logging.getLogger(__name__)


@receiver(post_save, sender=DisplayVideo)
def profile_video_post_save(sender, instance, created, **kwargs):
    if created and instance.video and instance.video.name:
        try:
            compress_video_task.delay("DisplayVideo", instance.pk)
            logger.info(f"Scheduled compression for video {instance.pk}")
        except Exception as e:
            logger.error(f"Failed to schedule compression for video {instance.pk}: {e}")


from django.db import transaction


@receiver(post_save, sender=VideoFile)
def video_file_post_save(sender, instance, created, **kwargs):
    if not created or not instance.file:
        return

    # Message upload flows may run compression synchronously before publishing WS.
    if getattr(instance, "_skip_auto_compress", False):
        return

    def enqueue():
        compress_video_task.delay("VideoFile", instance.pk)
        logger.info(f"Scheduled compression for VideoFile {instance.pk}")

    transaction.on_commit(enqueue)
