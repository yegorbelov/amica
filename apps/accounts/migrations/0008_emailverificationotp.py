import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_rename_acc_rec_usr_bind_accounts_de_user_id_8dba72_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailVerificationOtp",
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
                ("code_hash", models.CharField(max_length=64)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField()),
                ("consumed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_verification_otps",
                        to="accounts.customuser",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "consumed"],
                        name="accounts_em_user_id_6f1a8b_idx",
                    ),
                ],
            },
        ),
    ]
