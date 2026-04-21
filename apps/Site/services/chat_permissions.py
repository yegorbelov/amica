"""Who may post or react in a chat (channels are read-only for subscribers)."""

from apps.Site.models import Chat, ChatMember


def get_user_chat_role(chat: Chat, user) -> str | None:
    if chat.is_dialog:
        return None
    cm = ChatMember.objects.filter(chat_id=chat.id, user_id=user.id).first()
    return cm.role if cm else None


def user_can_post_in_chat(chat: Chat, user) -> bool:
    if not chat.is_channel:
        return True
    role = get_user_chat_role(chat, user)
    if role is None:
        return False
    return role in (ChatMember.Role.OWNER, ChatMember.Role.ADMIN)


def user_is_channel_subscriber(chat: Chat, user) -> bool:
    if not chat.is_channel:
        return False
    return get_user_chat_role(chat, user) == ChatMember.Role.SUBSCRIBER
