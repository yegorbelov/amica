import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_legacy_passkeys(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    UserWebAuthnCredential = apps.get_model("accounts", "UserWebAuthnCredential")
    for u in CustomUser.objects.exclude(credential_id__isnull=True).iterator():
        cid = u.credential_id
        pk = u.credential_public_key
        if not cid or not pk:
            continue
        UserWebAuthnCredential.objects.create(
            id=uuid.uuid4(),
            user_id=u.pk,
            credential_id=cid,
            public_key=pk,
            sign_count=u.sign_count or 0,
        )
    CustomUser.objects.filter(credential_id__isnull=False).update(
        credential_id=None,
        credential_public_key=None,
        sign_count=0,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_activesession_user_agent_hints"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserWebAuthnCredential",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("credential_id", models.BinaryField()),
                ("public_key", models.BinaryField()),
                ("sign_count", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="webauthn_credentials",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user"],
                        name="accounts_userwebauth_cred_user_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("credential_id",),
                        name="accounts_webauthn_cred_id_uniq",
                    ),
                ],
            },
        ),
        migrations.RunPython(migrate_legacy_passkeys, migrations.RunPython.noop),
    ]
