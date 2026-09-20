import os
import re
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core import mail
from django.test import LiveServerTestCase, SimpleTestCase, override_settings

from protection.crypto import decrypt_bytes, encrypt_bytes, generate_data_key

from .api_client import ApiClient, ApiError
from .backup_service import collect_files, rerun_backup, restore_version, run_backup
from .crypto import DecryptionFailed, decrypt, encrypt

EMAIL, PASSWORD = "client@example.com", "CorrectHorseBatteryStaple9"


def api_url(test):
    # LiveServerTestCase advertises "localhost", which Windows tries over IPv6
    # first, adding ~2s to every request; the test server listens on IPv4.
    return test.live_server_url.replace("localhost", "127.0.0.1") + "/api"


class CryptoTests(SimpleTestCase):
    def test_round_trip(self):
        key = generate_data_key().decode()
        self.assertEqual(decrypt(encrypt(b"hello", key), key), b"hello")

    def test_wrong_key_and_garbage_raise_decryption_failed(self):
        ciphertext = encrypt(b"hello", generate_data_key().decode())
        with self.assertRaises(DecryptionFailed):
            decrypt(ciphertext, generate_data_key().decode())
        with self.assertRaises(DecryptionFailed):
            decrypt(b"not a real token", generate_data_key().decode())

    def test_compatible_with_the_servers_crypto_in_both_directions(self):
        key = generate_data_key()
        self.assertEqual(decrypt_bytes(encrypt(b"from client", key.decode()), key), b"from client")
        self.assertEqual(decrypt(encrypt_bytes(b"from server", key), key.decode()), b"from server")


class CollectFilesTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "sub").mkdir()
        (self.root / "a.txt").write_text("a")
        (self.root / "sub" / "b.txt").write_text("b")

    def test_folders_are_expanded_recursively_and_duplicates_removed(self):
        files = collect_files([self.root, self.root / "a.txt"])
        self.assertEqual(sorted(f.name for f in files), ["a.txt", "b.txt"])

    def test_a_missing_path_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            collect_files([self.root / "nope.txt"])

    def test_an_empty_folder_yields_nothing(self):
        (self.root / "empty").mkdir()
        self.assertEqual(collect_files([self.root / "empty"]), [])


class ApiClientTests(SimpleTestCase):
    def test_an_unreachable_server_is_an_api_error_not_a_raw_exception(self):
        with self.assertRaises(ApiError) as caught:
            ApiClient("http://127.0.0.1:9/api").login("a@example.com", "x")
        self.assertIn("Could not reach the server", caught.exception.message)


class AuthTests(LiveServerTestCase):
    def setUp(self):
        self.api = ApiClient(api_url(self))

    def test_register_login_me_logout(self):
        self.api.register(EMAIL, "Client Test", PASSWORD, PASSWORD)
        self.assertEqual(self.api.login(EMAIL, PASSWORD)["email"], EMAIL)
        self.assertEqual(self.api.me()["email"], EMAIL)

        self.api.logout()
        with self.assertRaises(ApiError) as caught:
            self.api.me()
        self.assertEqual(caught.exception.status_code, 401)

    def test_wrong_password_and_duplicate_registration_give_readable_errors(self):
        self.api.register(EMAIL, "Client Test", PASSWORD, PASSWORD)
        with self.assertRaises(ApiError) as caught:
            self.api.login(EMAIL, "wrong-password")
        self.assertEqual(caught.exception.message, "Invalid email or password.")

        with self.assertRaises(ApiError) as caught:
            self.api.register(EMAIL, "Again", PASSWORD, PASSWORD)
        self.assertIn("already exists", caught.exception.message)

    def test_password_reset_round_trip(self):
        self.api.register(EMAIL, "Client Test", PASSWORD, PASSWORD)
        self.api.request_password_reset(EMAIL)
        code = re.search(r"^\S+:\S+$", mail.outbox[0].body, re.MULTILINE).group(0)

        new_password = "Another-Correct-Horse-77"
        self.api.confirm_password_reset(code, new_password, new_password)
        self.assertEqual(self.api.login(EMAIL, new_password)["email"], EMAIL)

    def test_too_many_failed_logins_are_refused_with_a_readable_message(self):
        self.api.register(EMAIL, "Client Test", PASSWORD, PASSWORD)
        for _ in range(5):
            with self.assertRaises(ApiError):
                self.api.login(EMAIL, "wrong-password")

        with self.assertRaises(ApiError) as caught:
            self.api.login(EMAIL, PASSWORD)  # even the right password is refused for now
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("Too many failed login attempts", caught.exception.message)


@override_settings(ALLOW_LOCAL_FOLDER_PROVIDERS=True)
class ClientFlowTestCase(LiveServerTestCase):
    provider_count = 3

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

        self.api = ApiClient(api_url(self))
        self.api.register(EMAIL, "Client Test", PASSWORD, PASSWORD)
        self.api.login(EMAIL, PASSWORD)

        self.folders = []
        for i in range(self.provider_count):
            folder = self.tmp / f"provider{i}"
            folder.mkdir()
            self.folders.append(folder)
            self.api.connect_provider("local_folder", f"provider{i}", {"path": str(folder)})

        self.source = self.tmp / "source"
        (self.source / "sub").mkdir(parents=True)
        self.notes = self.source / "notes.txt"
        self.data = self.source / "sub" / "data.bin"
        self.notes.write_text("SECRET-MARKER-notes " * 200)
        self.data.write_bytes(os.urandom(50_000))
        self.restored = self.tmp / "restored"

    def version_of(self, job_id, path, number=1):
        job = self.api.get_job(job_id)
        file = next(f for f in job["files"] if Path(f["original_path"]) == path.resolve())
        return next(v for v in file["versions"] if v["version_number"] == number)


class BackupAndRestoreTests(ClientFlowTestCase):
    def test_backup_and_restore_round_trip(self):
        stages, processed = [], []

        def progress(stage, detail, done, total):
            stages.append(stage)
            if stage == "processed":
                processed.append((done, total))

        result = run_backup(self.api, "docs", [self.source], progress=progress)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.failed, {})
        self.assertEqual(len(result.succeeded), 2)
        self.assertEqual((stages[0], stages[-1]), ("preparing", "completed"))
        self.assertLess(stages.index("protecting"), stages.index("uploading"))
        self.assertEqual(processed, [(1, 2), (2, 2)])

        stored = [p for folder in self.folders for p in folder.rglob("*.bin")]
        self.assertEqual(len(stored), 6)
        for path in stored:
            self.assertNotIn(b"SECRET-MARKER", path.read_bytes())

        for original in (self.notes, self.data):
            version = self.version_of(result.job_id, original)
            restored = restore_version(self.api, version["id"], self.restored / original.name)
            self.assertEqual(restored.read_bytes(), original.read_bytes())

        history = self.api.list_recoveries()
        self.assertEqual([op["status"] for op in history], ["completed", "completed"])

    def test_rerun_creates_a_new_version_and_the_old_one_stays_recoverable(self):
        result = run_backup(self.api, "docs", [self.notes])
        old = self.notes.read_bytes()
        self.notes.write_text("changed contents " * 50)
        new = self.notes.read_bytes()

        rerun = rerun_backup(self.api, result.job_id)
        self.assertEqual(rerun.status, "completed")

        for number, expected in ((1, old), (2, new)):
            version = self.version_of(result.job_id, self.notes, number)
            restored = restore_version(self.api, version["id"], self.restored / f"v{number}.txt")
            self.assertEqual(restored.read_bytes(), expected)

    def test_restore_survives_a_provider_outage(self):
        result = run_backup(self.api, "docs", [self.notes])
        shutil.rmtree(self.folders[0])

        version = self.version_of(result.job_id, self.notes)
        restored = restore_version(self.api, version["id"], self.restored / "notes.txt")
        self.assertEqual(restored.read_bytes(), self.notes.read_bytes())

    def test_restore_will_not_overwrite_unless_told_to(self):
        result = run_backup(self.api, "docs", [self.notes])
        version = self.version_of(result.job_id, self.notes)
        target = self.restored / "notes.txt"
        target.parent.mkdir()
        target.write_text("precious existing file")

        with self.assertRaises(FileExistsError):
            restore_version(self.api, version["id"], target)
        self.assertEqual(target.read_text(), "precious existing file")
        self.assertEqual(self.api.list_recoveries(), [])

        restore_version(self.api, version["id"], target, overwrite=True)
        self.assertEqual(target.read_bytes(), self.notes.read_bytes())

    def test_an_unreadable_file_fails_only_that_file(self):
        result = run_backup(self.api, "docs", [self.source])
        self.notes.unlink()

        rerun = rerun_backup(self.api, result.job_id)
        self.assertEqual(rerun.status, "failed")
        self.assertEqual(rerun.succeeded, [str(self.data.resolve())])
        self.assertEqual(list(rerun.failed), [str(self.notes.resolve())])

    def test_a_local_decryption_failure_is_reported_to_the_server(self):
        result = run_backup(self.api, "docs", [self.notes])
        version = self.version_of(result.job_id, self.notes)
        target = self.restored / "notes.txt"

        with mock.patch("client.backup_service.decrypt", side_effect=DecryptionFailed("boom")):
            with self.assertRaises(DecryptionFailed):
                restore_version(self.api, version["id"], target)

        self.assertFalse(target.exists())
        operation = self.api.list_recoveries()[0]
        self.assertEqual(operation["status"], "failed")
        self.assertIn("boom", operation["error_message"])

    def test_losing_too_many_providers_is_an_api_error_and_leaves_no_file(self):
        result = run_backup(self.api, "docs", [self.notes])
        version = self.version_of(result.job_id, self.notes)
        shutil.rmtree(self.folders[0])
        shutil.rmtree(self.folders[1])

        target = self.restored / "notes.txt"
        with self.assertRaises(ApiError) as caught:
            restore_version(self.api, version["id"], target)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertFalse(target.exists())
        self.assertEqual(self.api.list_recoveries()[0]["status"], "failed")

    def test_nothing_to_back_up_is_an_error(self):
        (self.tmp / "empty").mkdir()
        with self.assertRaises(ValueError):
            run_backup(self.api, "empty", [self.tmp / "empty"])


class SingleProviderClientTests(ClientFlowTestCase):
    provider_count = 1

    def test_backup_fails_clearly_when_there_is_no_redundancy(self):
        result = run_backup(self.api, "docs", [self.source])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.succeeded, [])
        self.assertEqual(len(result.failed), 2)
        self.assertTrue(all("at least 2" in message for message in result.failed.values()))
