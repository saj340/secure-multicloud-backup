import importlib
from unittest import mock

from django.apps import apps
from rest_framework.test import APIClient

from backups import test_manager
from backups.manager import BackupError

from .adapters.base import ProviderUnavailableError
from .adapters.local import LocalFolderAdapter
from .models import StorageLocation


def bytes_on_disk(folder):
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


class StorageUsageTests(test_manager.PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def usage_by_name(self):
        rows = self.api.get("/api/providers/").data
        return {row["display_name"]: row["storage_used_bytes"] for row in rows}

    def test_each_stored_fragment_records_its_real_size(self):
        self.client_backup()
        locations = StorageLocation.objects.all()
        self.assertEqual(locations.count(), 3)
        for location in locations:
            on_disk = (self.folders[location.fragment_index] / location.remote_path).stat().st_size
            self.assertGreater(location.size_bytes, 0)
            self.assertEqual(location.size_bytes, on_disk)

    def test_the_api_reports_what_each_provider_really_holds(self):
        self.client_backup()
        self.client_backup(file=self.job.files.get(original_path="C:/docs/notes.txt"))

        usage = self.usage_by_name()

        for index, folder in enumerate(self.folders):
            self.assertEqual(usage[f"provider{index}"], bytes_on_disk(folder))
        self.assertGreater(sum(usage.values()), 0)

    def test_a_provider_with_nothing_stored_shows_zero(self):
        self.assertEqual(self.usage_by_name(), {"provider0": 0, "provider1": 0, "provider2": 0})

    def test_a_backup_that_failed_part_way_is_not_counted(self):
        real_upload = LocalFolderAdapter.upload
        calls = []

        def second_upload_fails(adapter, remote_path, data):
            calls.append(remote_path)
            if len(calls) == 2:
                raise ProviderUnavailableError("down")
            return real_upload(adapter, remote_path, data)

        with mock.patch.object(LocalFolderAdapter, "upload", second_upload_fails):
            with self.assertRaises(BackupError):
                self.client_backup()

        self.assertEqual(self.usage_by_name(), {"provider0": 0, "provider1": 0, "provider2": 0})

    def test_a_single_provider_response_carries_the_total_too(self):
        self.client_backup()
        response = self.api.post(f"/api/providers/{self.providers[0].pk}/check/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["storage_used_bytes"], bytes_on_disk(self.folders[0]))


class BackfillMigrationTests(test_manager.PipelineTestCase):
    """Backups made before the column existed get their sizes from the saved fragment_size."""

    def run_backfill(self):
        migration = importlib.import_module("providers.migrations.0005_storagelocation_size_bytes")
        migration.fill_in_sizes(apps, None)

    def test_old_rows_are_filled_in_from_the_saved_fragment_size(self):
        self.client_backup()
        real_sizes = {loc.pk: loc.size_bytes for loc in StorageLocation.objects.all()}
        StorageLocation.objects.update(size_bytes=0)  # how a row from before the change looks

        self.run_backfill()

        self.assertEqual({loc.pk: loc.size_bytes for loc in StorageLocation.objects.all()}, real_sizes)

    def test_rows_that_already_have_a_size_are_left_alone(self):
        self.client_backup()
        StorageLocation.objects.update(size_bytes=7)
        self.run_backfill()
        self.assertEqual(set(StorageLocation.objects.values_list("size_bytes", flat=True)), {7})

    def test_a_version_without_saved_metadata_stays_at_zero_without_crashing(self):
        self.client_backup()
        version = StorageLocation.objects.first().file_version
        version.protection_metadata = {}
        version.save()
        StorageLocation.objects.update(size_bytes=0)

        self.run_backfill()

        self.assertEqual(set(StorageLocation.objects.values_list("size_bytes", flat=True)), {0})
