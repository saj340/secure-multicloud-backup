"""
Backing up and checking the master key (MASTER_KEK).

Every file's data key and every provider's credentials are stored encrypted under the
master key, so losing it makes every backup unreadable. Three tools protect against that:

    make_backup / read_backup   put the key in a file encrypted with a passphrase, to be
                                kept somewhere other than this computer
    check_key                   prove a key really decrypts what is stored in the database

No custom cryptography: the passphrase becomes a key with scrypt (deliberately slow and
memory-hungry, so guessing passphrases is expensive) and the key itself is encrypted with
Fernet, the same library call used everywhere else in the project.
"""

import base64
import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from backups.models import FileVersion
from providers.models import CloudProvider

BACKUP_FORMAT = 1
MIN_PASSPHRASE_LENGTH = 12
SCRYPT_COST = {"n": 2**15, "r": 8, "p": 1}
# A backup file is untrusted input: refuse parameters that would make reading it
# ruin the computer (a huge n asks for gigabytes of memory and minutes of work).
MAX_SCRYPT_N = 2**20
MAX_SCRYPT_R = 16
MAX_SCRYPT_P = 4


class KeyBackupError(Exception):
    """The backup could not be made or read; the message says why, in plain words."""


def fingerprint(kek: str) -> str:
    """A short label that identifies a key without revealing it (SHA-256 of a random key)."""
    return hashlib.sha256(kek.encode()).hexdigest()[:12]


def _passphrase_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    raw = Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode())
    return base64.urlsafe_b64encode(raw)


def make_backup(kek: str, passphrase: str) -> str:
    """The master key encrypted under the passphrase, as text that is safe to store."""
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise KeyBackupError(
            f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters long."
        )
    try:
        Fernet(kek.encode())
    except ValueError as exc:
        raise KeyBackupError("The master key is not a valid key.") from exc
    salt = os.urandom(16)
    key = _passphrase_key(passphrase, salt, **SCRYPT_COST)
    document = {
        "format": BACKUP_FORMAT,
        "kdf": "scrypt",
        **SCRYPT_COST,
        "salt": base64.b64encode(salt).decode(),
        "fingerprint": fingerprint(kek),
        "encrypted_key": Fernet(key).encrypt(kek.encode()).decode(),
    }
    return json.dumps(document, indent=2) + "\n"


def read_backup(text: str, passphrase: str) -> str:
    """The master key held in a backup, given the passphrase it was made with."""
    try:
        document = json.loads(text)
        if document["format"] != BACKUP_FORMAT or document["kdf"] != "scrypt":
            raise KeyBackupError("This backup was made by a different version and can't be read.")
        n, r, p = document["n"], document["r"], document["p"]
        salt = base64.b64decode(document["salt"], validate=True)
        token = document["encrypted_key"].encode()
        if not (isinstance(n, int) and isinstance(r, int) and isinstance(p, int)):
            raise ValueError
        is_power_of_two = (n & (n - 1)) == 0
        if not (2 <= n <= MAX_SCRYPT_N and is_power_of_two and 1 <= r <= MAX_SCRYPT_R
                and 1 <= p <= MAX_SCRYPT_P):
            raise KeyBackupError("This backup asks for unreasonable settings and was not opened.")
    except KeyBackupError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise KeyBackupError("This is not a valid master key backup file.") from exc
    try:
        return Fernet(_passphrase_key(passphrase, salt, n, r, p)).decrypt(token).decode()
    except (InvalidToken, ValueError) as exc:
        raise KeyBackupError("Wrong passphrase, or the backup file is damaged.") from exc


@dataclass
class KeyCheck:
    """How many stored secrets a key could and could not decrypt."""

    versions_ok: int = 0
    versions_bad: int = 0
    providers_ok: int = 0
    providers_bad: int = 0

    @property
    def checked(self) -> int:
        return self.versions_ok + self.versions_bad + self.providers_ok + self.providers_bad

    @property
    def bad(self) -> int:
        return self.versions_bad + self.providers_bad

    @property
    def works(self) -> bool:
        """True only if something was checked and all of it decrypted."""
        return self.checked > 0 and self.bad == 0

    def summary(self) -> str:
        return (
            f"{self.versions_ok} of {self.versions_ok + self.versions_bad} file keys and "
            f"{self.providers_ok} of {self.providers_ok + self.providers_bad} provider "
            "credentials decrypt."
        )


def check_key(kek: str) -> KeyCheck:
    """Try the key on every stored file data key and provider credential."""
    try:
        fernet = Fernet(kek.encode())
    except ValueError as exc:
        raise KeyBackupError("The master key is not a valid key.") from exc

    def opens(token) -> bool:
        try:
            fernet.decrypt(str(token).encode())
            return True
        except (InvalidToken, ValueError):
            return False

    result = KeyCheck()
    for metadata in FileVersion.objects.values_list("protection_metadata", flat=True).iterator():
        if opens(metadata.get("wrapped_key", "")):
            result.versions_ok += 1
        else:
            result.versions_bad += 1
    # A disconnected provider has had its credentials erased, so there is nothing to check.
    stored = CloudProvider.objects.exclude(encrypted_credentials="")
    for token in stored.values_list("encrypted_credentials", flat=True).iterator():
        if opens(token):
            result.providers_ok += 1
        else:
            result.providers_bad += 1
    return result
