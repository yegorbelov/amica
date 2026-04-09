from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_device_login_challenge_request_meta"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceloginchallenge",
            name="pending_otp",
            field=models.CharField(blank=True, default="", max_length=6),
        ),
    ]
