import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from monitoring.models import ActivityLog
from protection.crypto import decrypt_bytes, encrypt_bytes
from protection.models import IntegrityRecord
from providers.adapters.base import ProviderUnavailableError
from providers.adapters.local import LocalFolderAdapter
from providers.manager import connect_provider, disconnect_provider
from providers.models import CloudProvider, StorageLocation

from .manager import (
    BackupError,
    NotEnoughProvidersError,
    RecoveryError,
    begin_backup_run,
    complete_recovery,
    create_backup_job,
    fail_recovery,
    finish_backup_run,
    recover_ciphertext,
    recovery_data_key,
    start_file_version,
    start_recovery,
    store_ciphertext,
)
from .models import BackupJob, FileVersion, RecoveryOperation

LOCAL = CloudProvider.ProviderType.LOCAL_FOLDER
PLAINTEXT = b"Quarterly report: SECRET-MARKER-98765. " * 100


class PipelineTestCase(TestCase):
    provider_count = 3

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.user = User.objects.create_user(
            email="owner@example.com", password="irrelevant-pass-1"
        )

        self.folders = []
        self.providers = []
        for i in range(self.provider_count):
            folder = self.tmp_path / f"provider{i}"
            folder.mkdir()
            self.folders.append(folder)
            self.providers.append(
                connect_provider(self.user, LOCAL, f"provider{i}", {"path": str(folder)})
            )

        self.job = create_backup_job(
            self.user, "docs", ["C:/docs/report.txt", "C:/docs/notes.txt"]
        )
        self.file = self.job.files.get(original_path="C:/docs/report.txt")

    # Stand-ins for the desktop client, which does the encrypting/decrypting.
    def client_backup(self, file=None, plaintext=PLAINTEXT):
        version, data_key = start_file_version(file or self.file, "report.txt", len(plaintext))
        store_ciphertext(version, encrypt_bytes(plaintext, data_key))
        version.refresh_from_db()
        return version

    def client_recover(self, version, user=None):
        operation = start_recovery(user or self.user, version)
        ciphertext = recover_ciphertext(operation)
        plaintext = decrypt_bytes(ciphertext, recovery_data_key(operation))
        complete_recovery(operation)
        return plaintext, operation

    def stored_objects(self):
        return [p for folder in self.folders if folder.exists() for p in folder.rglob("*.bin")]


class BackupJobTests(PipelineTestCase):
    def test_job_records_files_as_given_without_duplicates(self):
        job = create_backup_job(self.user, "dupes", ["a.txt", "b.txt", "a.txt"])
        paths = list(job.files.order_by("id").values_list("original_path", flat=True))
        self.assertEqual(paths, ["a.txt", "b.txt"])
        self.assertEqual(job.status, BackupJob.Status.PENDING)

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            create_backup_job(self.user, "empty", [])


class BackupTests(PipelineTestCase):
    def test_backup_distributes_one_fragment_per_provider(self):
        version = self.client_backup()

        self.assertEqual(version.upload_status, FileVersion.UploadStatus.COMPLETED)
        locations = list(version.storage_locations.all())
        self.assertEqual([loc.fragment_index for loc in locations], [0, 1, 2])
        self.assertEqual({loc.cloud_provider_id for loc in locations}, {p.id for p in self.providers})
        self.assertTrue(
            all(
                loc.upload_status == StorageLocation.UploadStatus.COMPLETED and loc.uploaded_at
                for loc in locations
            )
        )
        for folder in self.folders:
            self.assertEqual(len(list(folder.rglob("*.bin"))), 1)
        self.assertEqual(version.protection_metadata["fragmentation"]["fragment_count"], 3)

    def test_providers_only_ever_receive_ciphertext_and_no_filenames(self):
        self.client_backup()
        objects = self.stored_objects()
        self.assertEqual(len(objects), 3)
        for path in objects:
            self.assertNotIn(b"SECRET-MARKER", path.read_bytes())
            self.assertNotIn("report", str(path.relative_to(self.tmp_path)))

    def test_plaintext_data_key_is_never_stored(self):
        version, data_key = start_file_version(self.file, "report.txt", 10)
        stored = json.dumps(version.protection_metadata)
        self.assertIn("wrapped_key", stored)
        self.assertNotIn(data_key.decode(), stored)

    def test_integrity_records_cover_the_whole_file_and_every_fragment(self):
        version = self.client_backup()
        records = version.integrity_records.all()
        self.assertEqual(records.filter(scope=IntegrityRecord.Scope.WHOLE_FILE).count(), 1)
        fragment_indexes = records.filter(scope=IntegrityRecord.Scope.FRAGMENT).values_list(
            "fragment_index", flat=True
        )
        self.assertEqual(sorted(fragment_indexes), [0, 1, 2])

    def test_backing_up_again_creates_a_new_version_and_keeps_the_old_one(self):
        first, second = b"first draft" * 20, b"second draft" * 20
        v1 = self.client_backup(plaintext=first)
        v2 = self.client_backup(plaintext=second)

        self.assertEqual((v1.version_number, v2.version_number), (1, 2))
        self.assertEqual(self.client_recover(v1)[0], first)
        self.assertEqual(self.client_recover(v2)[0], second)

    def test_an_unreachable_provider_is_skipped_and_the_backup_adapts(self):
        shutil.rmtree(self.folders[1])
        version = self.client_backup()

        self.assertEqual(version.protection_metadata["fragmentation"]["fragment_count"], 2)
        self.providers[1].refresh_from_db()
        self.assertEqual(self.providers[1].status, CloudProvider.Status.ERROR)
        self.assertEqual(self.client_recover(version)[0], PLAINTEXT)

    def test_upload_failure_cleans_up_and_fails_the_version(self):
        real_upload = LocalFolderAdapter.upload

        def flaky_upload(adapter, remote_path, data):
            if adapter.root == self.folders[2]:
                raise ProviderUnavailableError("simulated outage mid-upload")
            return real_upload(adapter, remote_path, data)

        with mock.patch.object(
            LocalFolderAdapter, "upload", autospec=True, side_effect=flaky_upload
        ):
            with self.assertRaises(BackupError):
                self.client_backup()

        version = self.file.versions.get()
        self.assertEqual(version.upload_status, FileVersion.UploadStatus.FAILED)
        self.assertEqual(self.stored_objects(), [])
        self.assertFalse(
            StorageLocation.objects.filter(file_version=version)
            .exclude(upload_status=StorageLocation.UploadStatus.FAILED)
            .exists()
        )
        self.assertEqual(version.integrity_records.count(), 0)
        self.assertTrue(ActivityLog.objects.filter(action="file_version_failed").exists())


class SingleProviderTests(PipelineTestCase):
    provider_count = 1

    def test_backup_needs_at_least_two_providers(self):
        version, data_key = start_file_version(self.file, "report.txt", 10)
        with self.assertRaises(NotEnoughProvidersError):
            store_ciphertext(version, encrypt_bytes(b"x", data_key))

        version.refresh_from_db()
        self.assertEqual(version.upload_status, FileVersion.UploadStatus.FAILED)
        self.assertEqual(self.stored_objects(), [])


class BackupRunStatusTests(PipelineTestCase):
    def test_run_completes_when_every_file_has_a_completed_version(self):
        begin_backup_run(self.job)
        self.assertEqual(self.job.status, BackupJob.Status.IN_PROGRESS)
        for file in self.job.files.all():
            self.client_backup(file=file)

        finish_backup_run(self.job)
        self.assertEqual(self.job.status, BackupJob.Status.COMPLETED)

    def test_run_fails_if_any_file_was_not_backed_up(self):
        begin_backup_run(self.job)
        self.client_backup()  # only one of the two files

        finish_backup_run(self.job)
        self.assertEqual(self.job.status, BackupJob.Status.FAILED)
        self.assertTrue(ActivityLog.objects.filter(action="backup_run_failed").exists())

    def test_a_rerun_that_skips_a_file_fails_even_though_its_old_version_completed(self):
        begin_backup_run(self.job)
        for file in self.job.files.all():
            self.client_backup(file=file)
        finish_backup_run(self.job)
        self.assertEqual(self.job.status, BackupJob.Status.COMPLETED)

        begin_backup_run(self.job)
        self.client_backup()  # second run only backs up one of the two files
        finish_backup_run(self.job)
        self.assertEqual(self.job.status, BackupJob.Status.FAILED)

    # The clock only ticks every couple of milliseconds, so two events can share a
    # timestamp. These freeze it completely, so that every timestamp is the same; a
    # judgement that depended on clock times could not tell the runs apart.

    def run_twice_with_a_frozen_clock(self, second_run_files):
        with mock.patch("django.utils.timezone.now", return_value=timezone.now()):
            begin_backup_run(self.job)
            for file in self.job.files.all():
                self.client_backup(file=file)
            finish_backup_run(self.job)
            self.assertEqual(self.job.status, BackupJob.Status.COMPLETED)

            begin_backup_run(self.job)
            for file in second_run_files:
                self.client_backup(file=file)
            finish_backup_run(self.job)

    def test_a_skipped_file_is_still_caught_when_every_timestamp_is_identical(self):
        self.run_twice_with_a_frozen_clock(list(self.job.files.all())[:1])
        self.assertEqual(self.job.status, BackupJob.Status.FAILED)

    def test_a_complete_rerun_still_succeeds_when_every_timestamp_is_identical(self):
        self.run_twice_with_a_frozen_clock(list(self.job.files.all()))
        self.assertEqual(self.job.status, BackupJob.Status.COMPLETED)

    def test_each_run_remembers_where_the_version_ids_had_got_to(self):
        begin_backup_run(self.job)
        self.assertEqual(self.job.last_run_version_floor, 0)
        for file in self.job.files.all():
            self.client_backup(file=file)
        newest = max(FileVersion.objects.values_list("id", flat=True))

        begin_backup_run(self.job)

        self.assertEqual(self.job.last_run_version_floor, newest)


class RecoveryTests(PipelineTestCase):
    def test_round_trip_is_byte_identical_and_recorded(self):
        version = self.client_backup()
        plaintext, operation = self.client_recover(version)

        self.assertEqual(plaintext, PLAINTEXT)
        operation.refresh_from_db()
        self.assertEqual(operation.status, RecoveryOperation.Status.COMPLETED)
        self.assertIsNotNone(operation.completed_at)
        self.assertFalse(version.integrity_records.exclude(is_valid=True).exists())

    def test_survives_one_provider_being_down(self):
        version = self.client_backup()
        shutil.rmtree(self.folders[0])
        self.assertEqual(self.client_recover(version)[0], PLAINTEXT)

    def test_survives_a_disconnected_provider(self):
        version = self.client_backup()
        disconnect_provider(self.providers[2])
        self.assertEqual(self.client_recover(version)[0], PLAINTEXT)

    def test_corrupted_fragment_is_detected_and_recovery_still_works(self):
        version = self.client_backup()
        next(self.folders[1].rglob("*.bin")).write_bytes(b"corrupted!" * 50)

        self.assertEqual(self.client_recover(version)[0], PLAINTEXT)
        bad = version.integrity_records.get(scope=IntegrityRecord.Scope.FRAGMENT, fragment_index=1)
        self.assertIs(bad.is_valid, False)

    def test_two_providers_down_fails_and_is_recorded(self):
        version = self.client_backup()
        shutil.rmtree(self.folders[0])
        shutil.rmtree(self.folders[1])

        operation = start_recovery(self.user, version)
        with self.assertRaises(RecoveryError):
            recover_ciphertext(operation)

        operation.refresh_from_db()
        self.assertEqual(operation.status, RecoveryOperation.Status.FAILED)
        self.assertTrue(operation.error_message)
        self.assertTrue(ActivityLog.objects.filter(action="recovery_failed").exists())

    def test_only_the_owner_can_recover(self):
        version = self.client_backup()
        stranger = User.objects.create_user(
            email="stranger@example.com", password="irrelevant-pass-1"
        )
        with self.assertRaises(PermissionDenied):
            start_recovery(stranger, version)
        self.assertEqual(RecoveryOperation.objects.count(), 0)

    def test_an_incomplete_version_cannot_be_recovered(self):
        version, _ = start_file_version(self.file, "report.txt", 10)
        with self.assertRaises(RecoveryError):
            start_recovery(self.user, version)

    def test_data_key_and_data_are_only_released_while_recovery_is_in_progress(self):
        version = self.client_backup()
        _, finished = self.client_recover(version)
        with self.assertRaises(RecoveryError):
            recovery_data_key(finished)
        with self.assertRaises(RecoveryError):
            recover_ciphertext(finished)

    def test_client_can_report_a_failed_local_restore(self):
        version = self.client_backup()
        operation = start_recovery(self.user, version)
        fail_recovery(operation, "Could not write the restored file.")

        operation.refresh_from_db()
        self.assertEqual(operation.status, RecoveryOperation.Status.FAILED)
        self.assertEqual(operation.error_message, "Could not write the restored file.")

    def test_backup_and_recovery_are_logged(self):
        version = self.client_backup()
        self.client_recover(version)
        actions = set(
            ActivityLog.objects.filter(user=self.user).values_list("action", flat=True)
        )
        self.assertTrue({"file_version_stored", "recovery_started", "recovery_completed"} <= actions)
