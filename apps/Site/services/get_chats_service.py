"""Service to build chat list for a user. Used by GetChats API and WebSocket get_chats."""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, OuterRef, Prefetch, Subquery

from apps.accounts.models.models import Profile
from apps.media_files.models.models import DisplayMedia
from apps.Site.models import (
    Chat,
    ChatMember,
    Contact,
    Message,
    MessageRecipient,
)
from apps.Site.serializers import ChatListSerializer

User = get_user_model()


def get_chats_list(user, chat_ids=None):
    """
    Return {"chats": [...]} for the given user.
    Same data as GetChats API; context uses user (no request) for serializer.

    chat_ids: optional list of chat primary keys to restrict results (e.g. one id
    after being added to a group).
    """
    last_message_subquery = Message.objects.filter(
        chat_id=OuterRef("pk"), deleted_at__isnull=True
    ).order_by("-date")

    member_count_subquery = (
        ChatMember.objects.filter(chat_id=OuterRef("pk"))
        .values("chat")
        .annotate(cnt=Count("id"))
        .values("cnt")
    )

    chats_qs = (
        Chat.objects.filter(users=user)
        .annotate(
            users_count=Subquery(member_count_subquery),
            last_message_id=Subquery(last_message_subquery.values("id")[:1]),
        )
        .order_by("-created_at")
        .prefetch_related(
            Prefetch(
                "chatmember_set",
                queryset=ChatMember.objects.exclude(user=user).select_related(
                    "user__profile"
                ),
                to_attr="other_members",
            )
        )
    )
    if chat_ids is not None:
        chats_qs = chats_qs.filter(id__in=chat_ids)

    dialog_interlocutor_ids = [
        member.user.id
        for chat in chats_qs
        if chat.is_dialog
        for member in getattr(chat, "other_members", [])
    ]

    contacts_qs = Contact.objects.filter(
        owner=user, user_id__in=dialog_interlocutor_ids
    ).select_related("user")
    contacts_map = {c.user_id: c for c in contacts_qs}

    last_message_ids = [
        chat.last_message_id for chat in chats_qs if chat.last_message_id
    ]
    recipients_prefetch = Prefetch(
        "recipients",
        queryset=MessageRecipient.objects.filter(read_date__isnull=False),
        to_attr="read_recipients",
    )

    messages_qs = (
        Message.objects.filter(id__in=last_message_ids)
        .select_related("user")
        .prefetch_related("file", recipients_prefetch, "message_reactions")
    )
    last_message_map = {m.chat_id: m for m in messages_qs}

    ct_chat = ContentType.objects.get_for_model(Chat).id
    ct_user = ContentType.objects.get_for_model(User).id
    ct_contact = ContentType.objects.get_for_model(Contact).id
    ct_profile = ContentType.objects.get_for_model(Profile).id

    object_tuples = []
    for chat in chats_qs:
        object_tuples.append((ct_chat, chat.id))
        if chat.is_dialog:
            for member in getattr(chat, "other_members", []):
                interlocutor = member.user
                object_tuples.append((ct_user, interlocutor.id))
                profile = getattr(interlocutor, "profile", None)
                if profile:
                    object_tuples.append((ct_profile, profile.id))
                if interlocutor.id in contacts_map:
                    object_tuples.append(
                        (ct_contact, contacts_map[interlocutor.id].id)
                    )

    media_map = {}
    if object_tuples:
        ctype_ids, object_ids = zip(*object_tuples)
        media_qs = DisplayMedia.objects.filter(
            is_primary=True,
            content_type_id__in=ctype_ids,
            object_id__in=object_ids,
        ).select_related("displayphoto", "displayvideo")
        media_map = {(dm.content_type_id, dm.object_id): dm for dm in media_qs}

    unread_map = dict(
        MessageRecipient.objects.filter(
            user=user,
            deleted_at__isnull=True,
            message__deleted_at__isnull=True,
            read_date__isnull=True,
        )
        .exclude(message__user=user)
        .exclude(message__chat__chat_type=Chat.ChatType.CHANNEL)
        .values("message__chat_id")
        .annotate(cnt=Count("id"))
        .values_list("message__chat_id", "cnt")
    )

    for chat in chats_qs:
        chat.last_message = last_message_map.get(chat.id)
        chat.unread_count = unread_map.get(chat.id, 0)

    my_roles_map = dict(
        ChatMember.objects.filter(
            user=user, chat_id__in=[c.id for c in chats_qs]
        ).values_list("chat_id", "role")
    )

    context = {
        "user": user,
        "user_id": user.id,
        "media_map": media_map,
        "contacts_map": contacts_map,
        "ct_contact_id": ct_contact,
        "ct_profile_id": ct_profile,
        "ct_chat_id": ct_chat,
        "my_roles_map": my_roles_map,
        "interlocutors_map": {
            chat.id: (
                chat.other_members[0].user if chat.other_members else None
            )
            for chat in chats_qs
            if chat.is_dialog
        },
    }

    serializer = ChatListSerializer(chats_qs, many=True, context=context)
    return {"chats": serializer.data}
