# Merge migration: parallel 0005 branches (email/recovery vs index renames).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_email_verify_recovery_fingerprint"),
        ("accounts", "0005_rename_acc_dl_user_st_accounts_de_user_id_67cfed_idx_and_more"),
    ]

    operations = []
