import json
import tempfile
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.models import User
from backups.manager import create_backup_job, start_file_version
from providers.manager import connect_provider, disconnect_provider
from providers.models import CloudProvider

from .crypto import DecryptionError, unwrap_key
from .keytools import KeyBackupError, check_key, fingerprint, make_backup, read_backup

PASSPHRASE = "correct horse battery"
LOCAL = CloudProvider.ProviderType.LOCAL_FOLDER


def new_key() -> str:
    return Fernet.generate_key().decode()


class KeyBackupFileTests(SimpleTestCase):
    def setUp(self):
        self.kek = new_key()

    def test_a_backup_opens_with_the_same_passphrase(self):
        self.assertEqual(read_backup(make_backup(self.kek, PASSPHRASE), PASSPHRASE), self.kek)

    def test_the_wrong_passphrase_is_refused(self):
        backup = make_backup(self.kek, PASSPHRASE)
        with self.assertRaisesMessage(KeyBackupError, "Wrong passphrase"):
            read_backup(backup, PASSPHRASE + "x")

    def test_the_file_contains_neither_the_key_nor_the_passphrase(self):
        backup = make_backup(self.kek, PASSPHRASE)
        self.assertNotIn(self.kek, backup)
        self.assertNotIn(PASSPHRASE, backup)

    def test_every_backup_gets_its_own_random_salt(self):
        # Compare the salts themselves: the whole files always differ anyway, because
        # Fernet adds its own random value, which would hide a constant salt.
        salts = {json.loads(make_backup(self.kek, PASSPHRASE))["salt"] for _ in range(5)}
        self.assertEqual(len(salts), 5)

    def test_short_passphrases_are_refused_but_the_minimum_length_is_allowed(self):
        with self.assertRaisesMessage(KeyBackupError, "at least 12"):
            make_backup(self.kek, "x" * 11)
        make_backup(self.kek, "x" * 12)

    def test_something_that_is_not_a_key_is_refused(self):
        with self.assertRaisesMessage(KeyBackupError, "not a valid key"):
            make_backup("not-a-key", PASSPHRASE)

    def test_the_fingerprint_identifies_a_key_without_revealing_it(self):
        label = fingerprint(self.kek)
        self.assertEqual(label, fingerprint(self.kek))
        self.assertNotEqual(label, fingerprint(new_key()))
        self.assertNotIn(label, self.kek)
        self.assertEqual(json.loads(make_backup(self.kek, PASSPHRASE))["fingerprint"], label)

    def test_a_tampered_file_is_refused(self):
        document = json.loads(make_backup(self.kek, PASSPHRASE))
        token = document["encrypted_key"]
        document["encrypted_key"] = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
        with self.assertRaises(KeyBackupError):
            read_backup(json.dumps(document), PASSPHRASE)

    def test_files_that_are_not_backups_are_refused(self):
        valid = json.loads(make_backup(self.kek, PASSPHRASE))
        broken = [
            "not json at all",
            "[]",
            "{}",
            json.dumps({**valid, "format": 99}),
            json.dumps({**valid, "salt": "***not base64***"}),
            json.dumps({k: v for k, v in valid.items() if k != "encrypted_key"}),
            json.dumps({**valid, "n": "big"}),
        ]
        for text in broken:
            with self.subTest(text=text[:40]), self.assertRaises(KeyBackupError):
                read_backup(text, PASSPHRASE)

    def test_a_file_asking_for_absurd_effort_is_refused_without_trying(self):
        valid = json.loads(make_backup(self.kek, PASSPHRASE))
        for field, value in [("n", 2**30), ("n", 3), ("n", 1), ("r", 1000), ("p", 1000), ("p", 0)]:
            with self.subTest(field=field, value=value):
                with mock.patch("protection.keytools.Scrypt") as scrypt:
                    with self.assertRaisesMessage(KeyBackupError, "unreasonable"):
                        read_backup(json.dumps({**valid, field: value}), PASSPHRASE)
                scrypt.assert_not_called()


class StoredDataTestCase(TestCase):
    """A database holding one file version and one connected provider, under the real key."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.user = User.objects.create_user(email="owner@example.com", password="irrelevant-pass-1")
        self.provider = connect_provider(self.user, LOCAL, "p0", {"path": self._tmp.name})
        self.file = create_backup_job(self.user, "docs", ["C:/docs/a.txt"]).files.get()
        self.version, self.data_key = start_file_version(self.file, "a.txt", 10)


class CheckKeyTests(StoredDataTestCase):
    def test_nothing_stored_means_nothing_proven(self):
        self.version.delete()
        self.provider.delete()
        result = check_key(settings.MASTER_KEK)
        self.assertEqual(result.checked, 0)
        self.assertFalse(result.works)

    def test_the_right_key_decrypts_everything(self):
        result = check_key(settings.MASTER_KEK)
        self.assertEqual((result.versions_ok, result.providers_ok), (1, 1))
        self.assertEqual(result.bad, 0)
        self.assertTrue(result.works)

    def test_a_different_key_decrypts_nothing(self):
        result = check_key(new_key())
        self.assertEqual((result.versions_bad, result.providers_bad), (1, 1))
        self.assertEqual(result.checked, 2)
        self.assertFalse(result.works)

    def test_one_damaged_secret_is_counted_and_the_rest_still_pass(self):
        second, _ = start_file_version(self.file, "a.txt", 10)
        second.protection_metadata = {**second.protection_metadata, "wrapped_key": "damaged"}
        second.save()
        result = check_key(settings.MASTER_KEK)
        self.assertEqual((result.versions_ok, result.versions_bad), (1, 1))
        self.assertFalse(result.works)

    def test_a_version_with_no_key_at_all_counts_as_bad(self):
        self.version.protection_metadata = {}
        self.version.save()
        self.assertEqual(check_key(settings.MASTER_KEK).versions_bad, 1)

    def test_a_disconnected_provider_has_nothing_to_check(self):
        disconnect_provider(self.provider)
        result = check_key(settings.MASTER_KEK)
        self.assertEqual(result.providers_ok + result.providers_bad, 0)

    def test_an_invalid_key_is_refused(self):
        with self.assertRaises(KeyBackupError):
            check_key("not-a-key")


def run(command, *args, passphrases=()):
    """Run a management command with the passphrase prompts answered; returns its output."""
    out = StringIO()
    prompts = (
        mock.patch(f"protection.management.commands.{command}.getpass", side_effect=list(passphrases))
        if passphrases else nullcontext()
    )
    with prompts:
        call_command(command, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


class MasterKeyCommandTests(StoredDataTestCase):
    def setUp(self):
        super().setUp()
        self._backup_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._backup_dir.cleanup)
        self.backup_file = Path(self._backup_dir.name) / "key-backup.json"

    def make_backup_file(self):
        run("backup_master_key", str(self.backup_file), passphrases=[PASSPHRASE, PASSPHRASE])

    def test_check_reports_a_working_key(self):
        self.assertIn("The key works.", run("check_master_key"))

    def test_check_fails_loudly_for_the_wrong_key(self):
        with override_settings(MASTER_KEK=new_key()):
            with self.assertRaisesMessage(CommandError, "can NOT be decrypted"):
                run("check_master_key")

    def test_backup_writes_a_file_that_opens_and_says_so(self):
        output = run("backup_master_key", str(self.backup_file), passphrases=[PASSPHRASE, PASSPHRASE])
        self.assertIn("read back successfully", output)
        self.assertEqual(read_backup(self.backup_file.read_text(), PASSPHRASE), settings.MASTER_KEK)

    def test_backup_refuses_to_overwrite_a_file(self):
        self.backup_file.write_text("precious")
        with self.assertRaisesMessage(CommandError, "already exists"):
            run("backup_master_key", str(self.backup_file), passphrases=[PASSPHRASE, PASSPHRASE])
        self.assertEqual(self.backup_file.read_text(), "precious")

    def test_backup_refuses_to_write_inside_the_project_folder(self):
        inside = Path(settings.BASE_DIR) / "key-backup-should-not-exist.json"
        try:
            with self.assertRaisesMessage(CommandError, "outside it"):
                run("backup_master_key", str(inside), passphrases=[PASSPHRASE, PASSPHRASE])
            self.assertFalse(inside.exists())
        finally:
            inside.unlink(missing_ok=True)

    def test_backup_refuses_when_the_two_passphrases_differ(self):
        with self.assertRaisesMessage(CommandError, "different"):
            run("backup_master_key", str(self.backup_file), passphrases=[PASSPHRASE, PASSPHRASE + "!"])
        self.assertFalse(self.backup_file.exists())

    def test_backup_leaves_no_file_when_the_passphrase_is_too_short(self):
        with self.assertRaisesMessage(CommandError, "at least 12"):
            run("backup_master_key", str(self.backup_file), passphrases=["short", "short"])
        self.assertFalse(self.backup_file.exists())

    def test_a_backup_that_does_not_read_back_is_deleted_and_reported(self):
        target = "protection.management.commands.backup_master_key.read_backup"
        with mock.patch(target, return_value=new_key()):  # reads back as a different key
            with self.assertRaisesMessage(CommandError, "did not give back the same key"):
                run("backup_master_key", str(self.backup_file), passphrases=[PASSPHRASE, PASSPHRASE])
        self.assertFalse(self.backup_file.exists())

    def test_backup_needs_an_existing_folder(self):
        missing = Path(self._backup_dir.name) / "no-such-folder" / "key.json"
        with self.assertRaisesMessage(CommandError, "does not exist"):
            run("backup_master_key", str(missing), passphrases=[PASSPHRASE, PASSPHRASE])

    def test_restore_with_the_wrong_passphrase_fails(self):
        self.make_backup_file()
        with self.assertRaisesMessage(CommandError, "Wrong passphrase"):
            run("restore_master_key", str(self.backup_file), passphrases=["not the passphrase"])

    def test_restore_of_a_missing_file_fails_cleanly(self):
        with self.assertRaisesMessage(CommandError, "Could not read"):
            run("restore_master_key", str(self.backup_file), passphrases=[PASSPHRASE])

    def test_restoring_the_current_key_says_nothing_needs_changing(self):
        self.make_backup_file()
        output = run("restore_master_key", str(self.backup_file), passphrases=[PASSPHRASE])
        self.assertIn("decrypts everything stored", output)
        self.assertIn("nothing to change", output)
        self.assertNotIn("MASTER_KEK=", output)

    def test_a_backup_of_some_other_key_is_not_accepted(self):
        other = Path(self._backup_dir.name) / "other.json"
        other.write_text(make_backup(new_key(), PASSPHRASE))
        with self.assertRaisesMessage(CommandError, "not the key for this database"):
            run("restore_master_key", str(other), passphrases=[PASSPHRASE])

    def test_losing_the_key_and_recovering_it_brings_the_data_back(self):
        """The whole point: a replaced key strands the data; the backup undoes that."""
        original = settings.MASTER_KEK
        self.make_backup_file()

        with override_settings(MASTER_KEK=new_key()):  # .env lost, a new key generated
            self.assertFalse(check_key(settings.MASTER_KEK).works)
            wrapped = self.version.protection_metadata["wrapped_key"]
            with self.assertRaises(DecryptionError):
                unwrap_key(wrapped)  # the stranded data key can't be unwrapped

            output = run("restore_master_key", str(self.backup_file), passphrases=[PASSPHRASE])
            recovered = [l for l in output.splitlines() if l.startswith("MASTER_KEK=")]
            self.assertEqual(recovered, [f"MASTER_KEK={original}"])

        with override_settings(MASTER_KEK=recovered[0].split("=", 1)[1]):
            self.assertTrue(check_key(settings.MASTER_KEK).works)
            self.assertEqual(unwrap_key(wrapped), self.data_key)
