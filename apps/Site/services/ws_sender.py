from apps.Site.models import Message

def send_ws_message(message, user_id):
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    from apps.Site.serializers import MessageSerializer

    chat = message.chat
    channel_layer = get_channel_layer()
    

    serialized_message = MessageSerializer(message, context={"user_id": user_id}).data
    user_ids = list(chat.users.values_list("id", flat=True))

    for user_id in user_ids:
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "chat_message",
                "chat_id": chat.id,
                "data": serialized_message,
            },
        )


def send_ws_message_deleted(chat_id, message_id):
    """Broadcast message_deleted to all chat users (e.g. when message is deleted via REST)."""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    from apps.Site.models import Chat

    chat_id = int(chat_id)
    message_id = int(message_id)
    try:
        chat = Chat.objects.get(id=chat_id)
    except Chat.DoesNotExist:
        return
    channel_layer = get_channel_layer()
    user_ids = list(chat.users.values_list("id", flat=True))

    for uid in user_ids:
        async_to_sync(channel_layer.group_send)(
            f"user_{uid}",
            {
                "type": "message_deleted",
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )