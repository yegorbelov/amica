"""One-time backup codes used as a second-factor fallback when TOTP is lost (hashed at rest)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from .models import CustomUser

BACKUP_CODE_COUNT = 10
# Readable: 4 groups of 4 (A-Z0-9 minus ambiguous)
_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1IL")


def _signing_key() -> bytes:
    sk = settings.SECRET_KEY
    return sk.encode() if isinstance(sk, str) else bytes(sk)


def normalize_backup_code_input(raw: str) -> str:
    s = (raw or "").upper().replace("-", "").replace(" ", "")
    return "".join(c for c in s if c in _ALPHABET)


def _hash_code(normalized: str) -> str:
    return hmac.new(
        _signing_key(), normalized.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _generate_one_plain() -> str:
    parts = []
    for _ in range(4):
        chunk = "".join(secrets.choice(_ALPHABET) for _ in range(4))
        parts.append(chunk)
    return "-".join(parts)


def create_backup_codes_for_user(user: CustomUser) -> list[str]:
    """Create BACKUP_CODE_COUNT new codes; return plaintext once. Caller saves rows."""
    from .models import AccountBackupCode

    plain_codes = [_generate_one_plain() for _ in range(BACKUP_CODE_COUNT)]
    rows = [
        AccountBackupCode(
            user=user,
            code_hash=_hash_code(normalize_backup_code_input(p)),
        )
        for p in plain_codes
    ]
    AccountBackupCode.objects.bulk_create(rows)
    return plain_codes


@transaction.atomic
def verify_and_consume_backup_code(user: CustomUser, raw_code: str) -> bool:
    from .models import AccountBackupCode

    normalized = normalize_backup_code_input(raw_code)
    if len(normalized) < 8:
        return False
    digest = _hash_code(normalized)
    row = (
        AccountBackupCode.objects.select_for_update()
        .filter(user=user, code_hash=digest, used_at__isnull=True)
        .first()
    )
    if not row:
        return False
    row.used_at = timezone.now()
    row.save(update_fields=["used_at"])
    return True


def user_has_unused_backup_codes(user: CustomUser) -> bool:
    from .models import AccountBackupCode

    return AccountBackupCode.objects.filter(
        user=user, used_at__isnull=True
    ).exists()


def issue_initial_backup_codes_if_needed(user: CustomUser) -> list[str] | None:
    """If user has no backup code rows, create and return plaintext list."""
    from .models import AccountBackupCode

    if AccountBackupCode.objects.filter(user=user).exists():
        return None
    return create_backup_codes_for_user(user)


@transaction.atomic
def regenerate_backup_codes(user: CustomUser) -> list[str]:
    from .models import AccountBackupCode

    AccountBackupCode.objects.filter(user=user).delete()
    return create_backup_codes_for_user(user)
