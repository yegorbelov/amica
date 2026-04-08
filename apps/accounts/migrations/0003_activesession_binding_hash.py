from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_profile_default_wallpaper_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="activesession",
            name="binding_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
