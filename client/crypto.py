"""
Client-side encryption. Files are encrypted here, on the user's device,
with a data key the server issues per file version; the server and the
cloud providers only ever see ciphertext.

Deliberately independent of the server code: the client is a separate
program and must not import Django. Compatibility with the server's
crypto is covered by tests.
"""

from cryptography.fernet import Fernet, InvalidToken


class DecryptionFailed(Exception):
    """The data could not be decrypted: wrong key, tampering, or corruption."""


def encrypt(plaintext: bytes, data_key: str) -> bytes:
    return Fernet(data_key.encode()).encrypt(plaintext)


def decrypt(ciphertext: bytes, data_key: str) -> bytes:
    try:
        return Fernet(data_key.encode()).decrypt(ciphertext)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionFailed(
            "The file could not be decrypted: wrong key or corrupted data."
        ) from exc
