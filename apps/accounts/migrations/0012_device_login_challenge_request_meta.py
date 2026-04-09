# Generated manually (was 0011; renumbered after parallel 0011 rename migration)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "accounts",
            "0011_rename_accounts_ba_user_used_idx_accounts_ac_user_id_73cb29_idx",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceloginchallenge",
            name="request_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deviceloginchallenge",
            name="request_user_agent",
            field=models.TextField(blank=True, default=""),
        ),
    ]
