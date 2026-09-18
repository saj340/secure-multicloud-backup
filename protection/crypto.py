"""
Local data protection: envelope encryption for backed-up files.

Each file version gets its own randomly generated data key (DEK). That
key encrypts the file, then gets "wrapped" (encrypted) itself under one
server-held master key (the KEK, from settings.MASTER_KEK) before being
stored. Cloud providers only ever receive ciphertext; the KEK never
leaves the server, and the plaintext DEK never touches the database.

Deliberately not derived from the user's password: our password reset
flow doesn't require the old password, so a password-derived key would
make every existing backup unrecoverable after a reset.
"""

import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class DecryptionError(Exception):
    """Raised when data cannot be decrypted: wrong key, tampering, or corruption."""


def _master_kek() -> bytes:
    return settings.MASTER_KEK.encode()


def generate_data_key() -> bytes:
    """A fresh, random key for encrypting exactly one file version."""
    return Fernet.generate_key()


def wrap_key(data_key: bytes) -> str:
    """Encrypt a data key under the server's master key, for storage."""
    return Fernet(_master_kek()).encrypt(data_key).decode()


def unwrap_key(wrapped_key: str) -> bytes:
    """Recover a plaintext data key from its wrapped (stored) form."""
    try:
        return Fernet(_master_kek()).decrypt(wrapped_key.encode())
    except (InvalidToken, ValueError, TypeError) as exc:
        # InvalidToken covers a bad HMAC/expired token; ValueError/TypeError
        # cover malformed input (e.g. corrupted base64) that never even
        # reaches the HMAC check. Either way, callers just need one
        # consistent failure signal.
        raise DecryptionError(
            "Could not unwrap data key — master key mismatch or corrupted data."
        ) from exc


def encrypt_bytes(plaintext: bytes, data_key: bytes) -> bytes:
    return Fernet(data_key).encrypt(plaintext)


def decrypt_bytes(ciphertext: bytes, data_key: bytes) -> bytes:
    try:
        return Fernet(data_key).decrypt(ciphertext)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError(
            "Could not decrypt data — wrong key or corrupted/tampered ciphertext."
        ) from exc


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_integrity(data: bytes, expected_hash: str) -> bool:
    return compute_sha256(data) == expected_hash


def protect_file_bytes(plaintext: bytes) -> dict:
    """
    Encrypt raw file bytes for storage. Returns the ciphertext, the
    protection metadata to persist on a FileVersion, and the ciphertext's
    hash to persist on an IntegrityRecord.
    """
    data_key = generate_data_key()
    ciphertext = encrypt_bytes(plaintext, data_key)
    return {
        "ciphertext": ciphertext,
        "protection_metadata": {
            "algorithm": "Fernet (AES-128-CBC + HMAC-SHA256)",
            "wrapped_key": wrap_key(data_key),
        },
        "ciphertext_hash": compute_sha256(ciphertext),
    }


def unprotect_file_bytes(ciphertext: bytes, protection_metadata: dict) -> bytes:
    """Reverse of protect_file_bytes, given the stored metadata."""
    data_key = unwrap_key(protection_metadata["wrapped_key"])
    return decrypt_bytes(ciphertext, data_key)
