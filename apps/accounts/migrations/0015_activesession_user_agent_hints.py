from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_customuser_totp"),
    ]

    operations = [
        migrations.AddField(
            model_name="activesession",
            name="user_agent_hints",
            field=models.TextField(blank=True, default=""),
        ),
    ]
