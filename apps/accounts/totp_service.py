import base64
import hashlib

import pyotp
from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    )
    return Fernet(key)


def encrypt_totp_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_totp_secret(cipher: str) -> str:
    if not cipher:
        return ""
    return _fernet().decrypt(cipher.encode()).decode()


def build_otpauth_uri(secret: str, email: str, issuer: str = "Amica") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp_code_against_cipher(cipher: str, code: str) -> bool:
    if not cipher or not code:
        return False
    try:
        secret = decrypt_totp_secret(cipher)
    except Exception:
        return False
    digits = str(code).strip().replace(" ", "")
    if not digits.isdigit() or len(digits) != 6:
        return False
    return bool(pyotp.TOTP(secret).verify(digits, valid_window=1))


def user_totp_gate_ok(user, totp_code: str) -> bool:
    """If TOTP is off, always OK. If on, code must verify."""
    if not getattr(user, "totp_enabled", False):
        return True
    cipher = (getattr(user, "totp_secret_cipher", None) or "").strip()
    return verify_totp_code_against_cipher(cipher, totp_code)


def generate_totp_secret() -> str:
    return pyotp.random_base32()
