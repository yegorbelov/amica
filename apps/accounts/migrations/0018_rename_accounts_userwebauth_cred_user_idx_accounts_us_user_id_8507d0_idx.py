# Django 6 may create the user index with a truncated name when applying 0016, so the
# physical index is already accounts_us_user_id_8507d0_idx — no ALTER INDEX needed.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_deviceloginchallenge_delivery"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameIndex(
                    model_name="userwebauthncredential",
                    new_name="accounts_us_user_id_8507d0_idx",
                    old_name="accounts_userwebauth_cred_user_idx",
                ),
            ],
        ),
    ]
