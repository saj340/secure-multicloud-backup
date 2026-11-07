"""
Two-factor login with time-based one-time codes (TOTP, RFC 6238).

The user's authenticator app and the server share a secret. Every 30 seconds both derive
the same 6-digit code from it and the clock, so knowing the password alone is not enough.
The codes come from the `pyotp` library, which is what Google Authenticator, Microsoft
Authenticator and Authy interoperate with; nothing here invents cryptography.

What this module adds on top of the library:
  * the shared secret is stored encrypted under the master key;
  * a code is accepted once only (a replay of a sniffed code fails);
  * one-time backup codes, kept only as hashes, for a lost phone.
"""

import hashlib
import secrets
import time
from hmac import compare_digest

import pyotp
from django.utils import timezone

from protection.crypto import decrypt_with_master_key, encrypt_with_master_key

from .models import BackupCode, User

ISSUER = "Secure Multi-Cloud Backup"
STEP_SECONDS = 30
# Codes from one step before or after are accepted: phone and server clocks drift a little.
DRIFT_STEPS = 1

BACKUP_CODE_COUNT = 8
BACKUP_CODE_LENGTH = 16  # 16 characters from a 32-symbol alphabet = 80 bits
# No 0/O/1/I: backup codes get read off a screen or paper and typed back.
BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _now() -> float:
    return time.time()


def _totp(user: User) -> pyotp.TOTP:
    return pyotp.TOTP(decrypt_with_master_key(user.totp_secret).decode(), interval=STEP_SECONDS)


def begin_enrolment(user: User) -> tuple[str, str]:
    """Create a fresh secret (not yet active). Returns (secret, otpauth:// URI for the QR code)."""
    secret = pyotp.random_base32()
    user.totp_secret = encrypt_with_master_key(secret.encode())
    user.totp_enabled = False
    user.totp_last_step = 0
    user.save(update_fields=["totp_secret", "totp_enabled", "totp_last_step"])
    uri = pyotp.TOTP(secret, interval=STEP_SECONDS).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return secret, uri


def has_pending_enrolment(user: User) -> bool:
    return bool(user.totp_secret) and not user.totp_enabled


def verify_totp(user: User, code: str) -> bool:
    """True if `code` is the current code (give or take clock drift) and was not used before."""
    code = "".join(code.split())
    # ASCII digits only: compare_digest refuses other text (it would crash on, say,
    # full-width digits, which str.isdigit() accepts).
    if not user.totp_secret or not (code.isascii() and code.isdigit()):
        return False
    totp = _totp(user)
    current = int(_now() // STEP_SECONDS)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if compare_digest(totp.generate_otp(step), code):
            # The update only succeeds if this step is newer than any accepted before, so a
            # code can't be used twice, and two requests racing with one code can't both win.
            moved = User.objects.filter(pk=user.pk, totp_last_step__lt=step).update(totp_last_step=step)
            user.totp_last_step = max(user.totp_last_step, step)
            return moved == 1
    return False


def _normalise_backup_code(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def _hash_backup_code(user: User, code: str) -> str:
    # The codes are long and random, so a plain fast hash can't be brute-forced from a leak.
    return hashlib.sha256(f"{user.pk}:{code}".encode()).hexdigest()


def generate_backup_codes(user: User) -> list[str]:
    """Replace the user's backup codes with new ones; the old ones stop working. Shown once."""
    codes = [
        "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(BACKUP_CODE_LENGTH))
        for _ in range(BACKUP_CODE_COUNT)
    ]
    user.backup_codes.all().delete()
    BackupCode.objects.bulk_create(
        BackupCode(user=user, code_hash=_hash_backup_code(user, code)) for code in codes
    )
    return ["-".join(code[i:i + 4] for i in range(0, BACKUP_CODE_LENGTH, 4)) for code in codes]


def backup_codes_left(user: User) -> int:
    return user.backup_codes.filter(used_at__isnull=True).count()


def use_backup_code(user: User, text: str) -> bool:
    code = _normalise_backup_code(text)
    # The "update where unused" is what makes a code single-use even under a race.
    used = BackupCode.objects.filter(
        user=user, code_hash=_hash_backup_code(user, code), used_at__isnull=True
    ).update(used_at=timezone.now())
    return used == 1


def verify_second_factor(user: User, text: str) -> str | None:
    """Check what the user typed: an authenticator code or a backup code.

    Returns "totp", "backup" or None.
    """
    if verify_totp(user, text):
        return "totp"
    if use_backup_code(user, text):
        return "backup"
    return None


def turn_off(user: User) -> None:
    user.totp_secret = ""
    user.totp_enabled = False
    user.totp_last_step = 0
    user.save(update_fields=["totp_secret", "totp_enabled", "totp_last_step"])
    user.backup_codes.all().delete()
