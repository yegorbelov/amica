from django.contrib.contenttypes.models import ContentType
from django.db.models import Count

from apps.accounts.models.models import Profile
from apps.media_files.models.models import DisplayMedia
from apps.Site.models import Chat, ChatMember, Contact
from apps.Site.serializers import ChatListSerializer


def search_groups_globally_for_user(user, query: str, limit: int = 40):
    q = (query or "").strip()
    if not q:
        return []

    lim = max(1, min(int(limit), 100))
    qs = (
        Chat.objects.filter(chat_type=Chat.ChatType.GROUP)
        .annotate(users_count=Count("chatmember", distinct=True))
        .filter(name__icontains=q)
        .order_by("-created_at")[:lim]
    )
    chats = list(qs)
    if not chats:
        return []

    ct_chat = ContentType.objects.get_for_model(Chat).id
    object_ids = [c.id for c in chats]
    media_qs = DisplayMedia.objects.filter(
        is_primary=True,
        content_type_id=ct_chat,
        object_id__in=object_ids,
    ).select_related("displayphoto", "displayvideo")
    media_map = {(dm.content_type_id, dm.object_id): dm for dm in media_qs}

    for c in chats:
        c.last_message = None
        c.unread_count = 0

    ct_contact = ContentType.objects.get_for_model(Contact).id
    ct_profile = ContentType.objects.get_for_model(Profile).id

    context = {
        "user": user,
        "user_id": user.id,
        "media_map": media_map,
        "contacts_map": {},
        "ct_contact_id": ct_contact,
        "ct_profile_id": ct_profile,
        "ct_chat_id": ct_chat,
        "interlocutors_map": {},
    }
    data = ChatListSerializer(chats, many=True, context=context).data
    member_chat_ids = set(
        ChatMember.objects.filter(user=user).values_list("chat_id", flat=True)
    )
    for row in data:
        row["is_member"] = row["id"] in member_chat_ids
    return data
