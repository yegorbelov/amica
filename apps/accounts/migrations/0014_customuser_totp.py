from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_deviceloginchallenge_pending_otp"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="totp_secret_cipher",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="customuser",
            name="totp_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
