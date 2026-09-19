import shutil
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from protection.crypto import decrypt_bytes, encrypt_bytes

from .models import BackupJob, FileVersion, RecoveryOperation

PLAINTEXT = b"Quarterly report: SECRET-MARKER-98765. " * 100
OCTET_STREAM = "application/octet-stream"


class BackupApiTestCase(TestCase):
    provider_count = 3

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.user = User.objects.create_user(email="api@example.com", password="irrelevant-pass-1")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.folders = []
        for i in range(self.provider_count):
            folder = self.tmp_path / f"provider{i}"
            folder.mkdir()
            self.folders.append(folder)
            response = self.client.post(
                reverse("providers:list"),
                {
                    "provider_type": "local_folder",
                    "display_name": f"provider{i}",
                    "credentials": {"path": str(folder)},
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.content)

        response = self.client.post(
            reverse("backups:job-list"),
            {"name": "docs", "paths": ["C:/docs/report.txt", "C:/docs/notes.txt"]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.job_id = response.data["id"]
        self.file_id = response.data["files"][0]["id"]

    def other_client(self, email="other@example.com"):
        other = User.objects.create_user(email=email, password="irrelevant-pass-1")
        client = APIClient()
        client.force_authenticate(other)
        return client

    # Stand-ins for the desktop client, which encrypts/decrypts locally.
    def api_start_version(self, file_id=None, plaintext=PLAINTEXT):
        return self.client.post(
            reverse("backups:version-create", args=[file_id or self.file_id]),
            {"original_filename": "report.txt", "original_size": len(plaintext)},
            format="json",
        )

    def api_backup(self, plaintext=PLAINTEXT, file_id=None):
        started = self.api_start_version(file_id, plaintext)
        self.assertEqual(started.status_code, 201, started.content)
        key = started.data["data_key"].encode()
        version_id = started.data["version"]["id"]
        uploaded = self.client.put(
            reverse("backups:version-ciphertext", args=[version_id]),
            encrypt_bytes(plaintext, key),
            content_type=OCTET_STREAM,
        )
        return version_id, uploaded

    def api_recover(self, version_id):
        started = self.client.post(
            reverse("backups:recovery-list"), {"version_id": version_id}, format="json"
        )
        self.assertEqual(started.status_code, 201, started.content)
        operation_id = started.data["id"]

        data = self.client.get(reverse("backups:recovery-ciphertext", args=[operation_id]))
        self.assertEqual(data.status_code, 200, data.content)
        key = self.client.get(reverse("backups:recovery-key", args=[operation_id])).data["data_key"]
        plaintext = decrypt_bytes(data.content, key.encode())

        self.client.post(reverse("backups:recovery-complete", args=[operation_id]))
        return plaintext, operation_id


class JobApiTests(BackupApiTestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        for url in (
            reverse("backups:job-list"),
            reverse("backups:recovery-list"),
            reverse("backups:job-detail", args=[self.job_id]),
        ):
            with self.subTest(url):
                self.assertEqual(anonymous.get(url).status_code, 401)

    def test_create_list_and_detail(self):
        listed = self.client.get(reverse("backups:job-list")).data
        self.assertEqual([(j["name"], j["file_count"]) for j in listed], [("docs", 2)])

        detail = self.client.get(reverse("backups:job-detail", args=[self.job_id])).data
        self.assertEqual(
            sorted(f["original_path"] for f in detail["files"]),
            ["C:/docs/notes.txt", "C:/docs/report.txt"],
        )

    def test_empty_selection_is_a_validation_error(self):
        response = self.client.post(
            reverse("backups:job-list"), {"name": "empty", "paths": []}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_other_users_cannot_see_or_run_a_job(self):
        other = self.other_client()
        self.assertEqual(other.get(reverse("backups:job-list")).data, [])
        for name in ("backups:job-detail", "backups:job-begin", "backups:job-finish"):
            with self.subTest(name):
                url = reverse(name, args=[self.job_id])
                response = other.get(url) if name == "backups:job-detail" else other.post(url)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(BackupJob.objects.get().status, BackupJob.Status.PENDING)


class BackupAndRecoveryApiTests(BackupApiTestCase):
    def test_full_backup_and_recovery_over_the_api(self):
        begun = self.client.post(reverse("backups:job-begin", args=[self.job_id]))
        self.assertEqual(begun.data["status"], "in_progress")

        job = self.client.get(reverse("backups:job-detail", args=[self.job_id])).data
        versions = {}
        for file in job["files"]:
            content = b"contents of " + file["original_path"].encode()
            versions[file["id"]] = (content, *self.api_backup(content, file_id=file["id"]))
            self.assertEqual(versions[file["id"]][2].status_code, 200)
            self.assertEqual(versions[file["id"]][2].data["upload_status"], "completed")

        finished = self.client.post(reverse("backups:job-finish", args=[self.job_id]))
        self.assertEqual(finished.data["status"], "completed")

        for content, version_id, _ in versions.values():
            plaintext, _ = self.api_recover(version_id)
            self.assertEqual(plaintext, content)

        history = self.client.get(reverse("backups:recovery-list")).data
        self.assertEqual(len(history), 2)
        self.assertTrue(all(op["status"] == "completed" for op in history))

    def test_providers_never_hold_plaintext(self):
        self.api_backup()
        stored = [p for folder in self.folders for p in folder.rglob("*.bin")]
        self.assertEqual(len(stored), 3)
        for path in stored:
            self.assertNotIn(b"SECRET-MARKER", path.read_bytes())

    def test_recovery_survives_a_provider_outage(self):
        version_id, _ = self.api_backup()
        shutil.rmtree(self.folders[1])
        self.assertEqual(self.api_recover(version_id)[0], PLAINTEXT)

    def test_recovery_with_too_many_providers_down_fails_cleanly(self):
        version_id, _ = self.api_backup()
        shutil.rmtree(self.folders[0])
        shutil.rmtree(self.folders[1])

        started = self.client.post(
            reverse("backups:recovery-list"), {"version_id": version_id}, format="json"
        )
        response = self.client.get(
            reverse("backups:recovery-ciphertext", args=[started.data["id"]])
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RecoveryOperation.objects.get().status, RecoveryOperation.Status.FAILED)

    def test_uploading_the_same_version_twice_conflicts(self):
        version_id, first = self.api_backup()
        self.assertEqual(first.status_code, 200)
        second = self.client.put(
            reverse("backups:version-ciphertext", args=[version_id]),
            b"more data",
            content_type=OCTET_STREAM,
        )
        self.assertEqual(second.status_code, 409)

    def test_upload_validation(self):
        started = self.api_start_version()
        url = reverse("backups:version-ciphertext", args=[started.data["version"]["id"]])

        wrong_type = self.client.put(url, {"x": 1}, format="json")
        self.assertEqual(wrong_type.status_code, 415)

        empty = self.client.put(url, b"", content_type=OCTET_STREAM)
        self.assertEqual(empty.status_code, 400)

        with override_settings(MAX_UPLOAD_BYTES=100):
            too_big = self.client.put(url, b"x" * 200, content_type=OCTET_STREAM)
            self.assertEqual(too_big.status_code, 413)

        self.assertEqual(FileVersion.objects.get().upload_status, FileVersion.UploadStatus.PENDING)

    def test_a_finished_recovery_releases_neither_data_nor_key(self):
        version_id, _ = self.api_backup()
        _, operation_id = self.api_recover(version_id)

        for name in ("backups:recovery-ciphertext", "backups:recovery-key"):
            with self.subTest(name):
                response = self.client.get(reverse(name, args=[operation_id]))
                self.assertEqual(response.status_code, 409)
        complete_again = self.client.post(reverse("backups:recovery-complete", args=[operation_id]))
        self.assertEqual(complete_again.status_code, 409)

    def test_client_can_report_a_failed_local_restore(self):
        version_id, _ = self.api_backup()
        started = self.client.post(
            reverse("backups:recovery-list"), {"version_id": version_id}, format="json"
        )
        response = self.client.post(
            reverse("backups:recovery-fail", args=[started.data["id"]]),
            {"message": "Could not write the restored file."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(response.data["error_message"], "Could not write the restored file.")

    def test_an_incomplete_version_cannot_be_recovered(self):
        started = self.api_start_version()
        response = self.client.post(
            reverse("backups:recovery-list"),
            {"version_id": started.data["version"]["id"]},
            format="json",
        )
        self.assertEqual(response.status_code, 409)


class OtherUsersApiTests(BackupApiTestCase):
    def test_other_users_cannot_touch_versions_or_recoveries(self):
        version_id, _ = self.api_backup()
        started = self.client.post(
            reverse("backups:recovery-list"), {"version_id": version_id}, format="json"
        )
        operation_id = started.data["id"]
        other = self.other_client()

        self.assertEqual(
            other.post(
                reverse("backups:version-create", args=[self.file_id]),
                {"original_filename": "x", "original_size": 1},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            other.put(
                reverse("backups:version-ciphertext", args=[version_id]),
                b"data",
                content_type=OCTET_STREAM,
            ).status_code,
            404,
        )
        self.assertEqual(
            other.post(
                reverse("backups:recovery-list"), {"version_id": version_id}, format="json"
            ).status_code,
            404,
        )
        for name in ("backups:recovery-ciphertext", "backups:recovery-key"):
            with self.subTest(name):
                self.assertEqual(other.get(reverse(name, args=[operation_id])).status_code, 404)
        for name in ("backups:recovery-complete", "backups:recovery-fail"):
            with self.subTest(name):
                self.assertEqual(other.post(reverse(name, args=[operation_id])).status_code, 404)
        self.assertEqual(other.get(reverse("backups:recovery-list")).data, [])


class SingleProviderApiTests(BackupApiTestCase):
    provider_count = 1

    def test_upload_with_too_few_providers_is_unavailable(self):
        version_id, response = self.api_backup()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(FileVersion.objects.get().upload_status, FileVersion.UploadStatus.FAILED)
