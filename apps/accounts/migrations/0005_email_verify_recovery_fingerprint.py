import uuid

import django.db.models.deletion
from django.db import migrations, models


def set_existing_users_email_verified(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    from django.utils import timezone

    CustomUser.objects.filter(email_verified_at__isnull=True).update(
        email_verified_at=timezone.now()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_trusted_device_and_device_login_challenge"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DeviceRecoveryCooldown",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("binding_hash", models.CharField(max_length=64)),
                ("cooldown_until", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="device_recovery_cooldowns",
                        to="accounts.customuser",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "binding_hash"],
                        name="acc_rec_usr_bind",
                    ),
                ],
                "unique_together": {("user", "binding_hash")},
            },
        ),
        migrations.CreateModel(
            name="RecoveryEmailOtp",
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
                ("binding_hash", models.CharField(max_length=64)),
                ("code_hash", models.CharField(max_length=64)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField()),
                ("consumed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recovery_email_otps",
                        to="accounts.customuser",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "consumed"], name="acc_recotp_usr"),
                ],
            },
        ),
        migrations.RunPython(set_existing_users_email_verified, migrations.RunPython.noop),
    ]
