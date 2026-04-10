from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_userwebauthncredential"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceloginchallenge",
            name="delivery",
            field=models.CharField(
                choices=[
                    ("trusted_device", "Trusted device (WS)"),
                    ("email", "Email OTP"),
                ],
                default="trusted_device",
                max_length=20,
            ),
        ),
    ]
