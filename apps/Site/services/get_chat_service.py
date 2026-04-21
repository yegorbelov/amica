"""Service to build single chat with messages for a user. Used by GetChat API and WebSocket get_chat."""

from collections import defaultdict

from django.db.models import Count, Prefetch, Q

from apps.Site.models import Chat, Contact, Message, MessageRecipient
from apps.Site.serializers import ChatSerializer

PAGE_SIZE = 25


def _message_file_ids_by_message_id(message_pks):
    """One query: M2M through row order (by pk) per message. Empty input → {}."""
    if not message_pks:
        return {}
    through = Message.file.through
    msg_fk = through._meta.get_field("message").attname
    file_fk = through._meta.get_field("file").attname
    order_map = defaultdict(list)
    rows = (
        through.objects.filter(**{f"{msg_fk}__in": message_pks})
        .order_by(msg_fk, "pk")
        .values_list(msg_fk, file_fk)
    )
    for mid, fid in rows:
        order_map[mid].append(fid)
    return dict(order_map)


def _read_recipients_prefetch():
    return Prefetch(
        "recipients",
        queryset=MessageRecipient.objects.filter(
            read_date__isnull=False
        ).select_related("user"),
        to_attr="read_recipients",
    )


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
        Chat.objects.filter(id=chat_id)
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

    is_member = user is not None and chat.users.filter(pk=user.pk).exists()
    if not chat.is_channel and not is_member:
        raise Chat.DoesNotExist("Chat not found")

    # Channels: list messages by chat only (no MessageRecipient join). New subscribers
    # see full history; DM/groups keep per-recipient visibility.
    if chat.is_channel:
        if cursor_newer is not None:
            messages_qs = (
                Message.objects.filter(
                    chat_id=chat_id,
                    deleted_at__isnull=True,
                    id__gt=cursor_newer,
                )
                .select_related("user")
                .prefetch_related("file", "message_reactions")
                .annotate(
                    view_count=Count(
                        "recipients",
                        filter=Q(recipients__read_date__isnull=False),
                    )
                )
                .order_by("date")
            )
            messages = list(messages_qs[:page_size])
            next_newer_cursor = (
                messages[-1].id if len(messages) == page_size and messages else None
            )
            next_cursor = None
        else:
            messages_qs = (
                Message.objects.filter(
                    chat_id=chat_id,
                    deleted_at__isnull=True,
                )
                .select_related("user")
                .prefetch_related("file", "message_reactions")
                .annotate(
                    view_count=Count(
                        "recipients",
                        filter=Q(recipients__read_date__isnull=False),
                    )
                )
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
    elif cursor_newer is not None:
        messages_qs = (
            Message.objects.filter(
                chat_id=chat_id,
                deleted_at__isnull=True,
                id__gt=cursor_newer,
                recipients__user=user,
                recipients__deleted_at__isnull=True,
            )
            .select_related("user")
            .prefetch_related("file", _read_recipients_prefetch(), "message_reactions")
            .order_by("date")
        )
        messages = list(messages_qs[:page_size])
        next_newer_cursor = (
            messages[-1].id if len(messages) == page_size and messages else None
        )
        next_cursor = None
    else:
        messages_qs = (
            Message.objects.filter(
                chat_id=chat_id,
                deleted_at__isnull=True,
                recipients__user=user,
                recipients__deleted_at__isnull=True,
            )
            .select_related("user")
            .prefetch_related("file", _read_recipients_prefetch(), "message_reactions")
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

    message_pks = [m.pk for m in messages]
    file_order_map = _message_file_ids_by_message_id(message_pks)

    dialog_contact = None
    dialog_contact_interlocutor_id = None
    if chat.is_dialog:
        others = [u for u in chat.users.all() if u.pk != user.pk]
        interlocutor = others[0] if others else None
        if interlocutor is not None:
            dialog_contact_interlocutor_id = interlocutor.pk
            dialog_contact = (
                Contact.objects.filter(owner=user, user=interlocutor)
                .prefetch_related("display_media")
                .first()
            )

    chat._prefetched_objects_cache = {
        "messages": messages,
    }

    serializer = ChatSerializer(
        chat,
        context={
            "user": user,
            "user_id": user.id,
            "message_file_ids_by_message_id": file_order_map,
            "dialog_contact": dialog_contact,
            "dialog_contact_interlocutor_id": dialog_contact_interlocutor_id,
            "channel_messages": chat.is_channel,
        },
    )
    data = serializer.data
    data["next_cursor"] = next_cursor
    data["next_newer_cursor"] = next_newer_cursor
    return data
