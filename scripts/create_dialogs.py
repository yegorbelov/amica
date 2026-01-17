import random

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.Site.models import Chat


def run():
    User = get_user_model()
    users = list(User.objects.all())

    if len(users) < 2:
        print("Not enough users.")
        return

    created_count = 0

    for i, user1 in enumerate(users):
        for user2 in users[i + 1 :]:
            if random.random() < 0.3:
                try:
                    with transaction.atomic():
                        chat, created = Chat.get_or_create_direct_chat(user1, user2)
                        if created:
                            created_count += 1
                            print(
                                f"Dialog created: {user1.email} ↔ {user2.email} (Chat ID: {chat.id})"
                            )
                except Exception as e:
                    print(f"Error creating dialog {user1.email} ↔ {user2.email}: {e}")

    print(f"Total dialogs created: {created_count}")
