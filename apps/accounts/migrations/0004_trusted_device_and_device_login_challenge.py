import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_activesession_binding_hash"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="trusted_binding_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.CreateModel(
            name="DeviceLoginChallenge",
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
                ("new_binding_hash", models.CharField(max_length=64)),
                ("code_hash", models.CharField(max_length=64)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("expired", "Expired"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="device_login_challenges",
                        to="accounts.customuser",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "status"], name="acc_dl_user_st"),
                    models.Index(fields=["expires_at"], name="acc_dl_exp"),
                ],
            },
        ),
    ]
