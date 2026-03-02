"""Service to build single chat with messages for a user. Used by GetChat API and WebSocket get_chat."""

from django.db.models import Prefetch

from apps.Site.models import Chat, Message
from apps.Site.serializers import ChatSerializer


def get_chat_for_user(chat_id, user):
    """
    Return chat serialized with messages, media, members.
    Same data as GetChat API; context uses user (no request).
    Raises Chat.DoesNotExist if chat not found or user not in chat.
    """
    chat = (
        Chat.objects.filter(id=chat_id, users=user)
        .prefetch_related(
            "users",
            "display_media",
            "users__profile",
            "users__profile__profile_media",
            Prefetch(
                "messages",
                queryset=Message.objects.filter(is_deleted=False)
                .select_related("user")
                .prefetch_related("file")
                .order_by("date"),
            ),
        )
        .first()
    )
    if not chat:
        raise Chat.DoesNotExist("Chat not found")

    serializer = ChatSerializer(
        chat,
        context={
            "user": user,
            "user_id": user.id,
        },
    )
    return serializer.data
