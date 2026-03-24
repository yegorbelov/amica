"""Create a group chat and return ChatListSerializer-shaped payload (incl. members)."""

from apps.Site.models import Chat, ChatMember
from apps.Site.serializers import ChatListSerializer, ChatUserSerializer


def create_group_and_serialize(user, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Group name cannot be empty")
    if len(name) > 64:
        raise ValueError("Group name too long")

    chat = Chat.objects.create(
        chat_type=Chat.ChatType.GROUP,
        name=name,
    )
    ChatMember.objects.create(
        chat=chat,
        user=user,
        role=ChatMember.Role.OWNER,
    )
    chat = (
        Chat.objects.filter(id=chat.id)
        .prefetch_related(
            "users",
            "users__profile",
            "users__profile__profile_media",
        )
        .first()
    )
    chat.last_message = None
    chat.unread_count = 0
    chat.users_count = chat.users.count()

    context = {
        "user": user,
        "user_id": user.id,
        "interlocutors_map": {chat.pk: chat.get_interlocutor(user)},
        "contacts_map": {},
        "media_map": {},
    }
    list_data = ChatListSerializer(chat, context=context).data
    other_users = list(chat.users.exclude(pk=user.pk))
    list_data["members"] = ChatUserSerializer(
        other_users, many=True, context=context
    ).data
    return list_data
