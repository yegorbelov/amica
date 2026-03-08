"""Service to build single chat with messages for a user. Used by GetChat API and WebSocket get_chat."""

from apps.Site.models import Chat, Message
from apps.Site.serializers import ChatSerializer

PAGE_SIZE = 25


def get_chat_for_user(
    chat_id, user, cursor=None, cursor_newer=None, page_size=PAGE_SIZE
):
    """
    Return chat serialized with a page of messages (cursor-based, page_size messages).
    Same data as GetChat API; context uses user (no request).
    Raises Chat.DoesNotExist if chat not found or user not in chat.

    cursor: message id (exclusive) — return messages older than this (smaller id).
    cursor_newer: message id (exclusive) — return messages newer than this (greater id).
    None for both = first page (newest page_size messages in chronological order).
    Returns: chat dict with messages, media, members, next_cursor (for older), next_newer_cursor (for newer).
    """
    chat = (
        Chat.objects.filter(id=chat_id, users=user)
        .prefetch_related(
            "users",
            "display_media",
            "users__profile",
            "users__profile__profile_media",
        )
        .first()
    )
    if not chat:
        raise Chat.DoesNotExist("Chat not found")

    if cursor_newer is not None:
        messages_qs = (
            Message.objects.filter(
                chat_id=chat_id, is_deleted=False, id__gt=cursor_newer
            )
            .select_related("user")
            .prefetch_related("file")
            .order_by("date")
        )
        messages = list(messages_qs[:page_size])
        next_newer_cursor = (
            messages[-1].id if len(messages) == page_size and messages else None
        )
        next_cursor = None
    else:
        messages_qs = (
            Message.objects.filter(chat_id=chat_id, is_deleted=False)
            .select_related("user")
            .prefetch_related("file")
            .order_by("-date")
        )
        if cursor is not None:
            messages_qs = messages_qs.filter(id__lt=cursor)
        messages = list(messages_qs[:page_size])
        messages.reverse()
        next_cursor = (
            messages[0].id if len(messages) == page_size and messages else None
        )
        next_newer_cursor = None

    chat._prefetched_objects_cache = {
        "messages": messages,
    }

    serializer = ChatSerializer(
        chat,
        context={
            "user": user,
            "user_id": user.id,
        },
    )
    data = serializer.data
    data["next_cursor"] = next_cursor
    data["next_newer_cursor"] = next_newer_cursor
    return data
