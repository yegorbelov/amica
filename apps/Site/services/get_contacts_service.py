"""Service to build contacts list for a user. Used by WebSocket get_contacts."""

from django.contrib.auth import get_user_model
from django.db.models import Prefetch

from apps.Site.models import Chat, Contact
from apps.Site.serializers import ContactSerializer

User = get_user_model()


def get_contacts_for_user(user):
    """Return {"contacts": [...]} for the given user (dialog_map from DB, no request)."""
    contacts = (
        Contact.objects.filter(owner=user)
        .select_related("user", "user__profile")
        .prefetch_related(
            "display_media",
            "user__profile__profile_media",
        )
    )

    dialogs = (
        Chat.objects.filter(chat_type=Chat.ChatType.DIALOG, users=user).prefetch_related(
            Prefetch("users", queryset=User.objects.only("id"))
        )
    )

    dialog_map = {}
    for chat in dialogs:
        for u in chat.users.all():
            if u.id != user.id:
                dialog_map[u.id] = chat.id

    serializer = ContactSerializer(
        contacts,
        many=True,
        context={
            "user": user,
            "dialog_map": dialog_map,
        },
    )
    return {"contacts": serializer.data}
