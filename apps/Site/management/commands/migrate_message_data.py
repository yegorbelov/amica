from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models.signals import post_save
from Site.models import Message, MessageRecipient


class Command(BaseCommand):
    def handle(self, *args, **options):
        User = get_user_model()

        from Site.signals import create_message_recipients

        post_save.disconnect(create_message_recipients, sender=Message)

        try:
            for message in Message.objects.all():
                chat_users = message.chat.users.all()

                recipients = []
                for user in chat_users:
                    is_deleted = not message.allowed_users.filter(id=user.id).exists()

                    read_date = None
                    if message.viewed.filter(id=user.id).exists():
                        read_date = message.date

                    recipients.append(
                        MessageRecipient(
                            message=message,
                            user=user,
                            deleted_at=deleted_at,
                            read_date=read_date,
                        )
                    )

                MessageRecipient.objects.bulk_create(recipients)

                self.stdout.write(f"Migrated message {message.id}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            raise

        finally:
            post_save.connect(create_message_recipients, sender=Message)
            self.stdout.write("Signals reconnected")
