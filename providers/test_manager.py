import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

from accounts.models import User
from backups.models import BackupJob, File, FileVersion

from .adapters.base import ProviderConfigurationError, ProviderUnavailableError
from .adapters.local import LocalFolderAdapter
from .manager import (
    check_provider,
    connect_provider,
    disconnect_provider,
    get_adapter,
    get_credentials,
    set_credentials,
)
from .models import CloudProvider, StorageLocation

LOCAL = CloudProvider.ProviderType.LOCAL_FOLDER


class ManagerTestCase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.user = User.objects.create_user(email="mgr@example.com", password="irrelevant-pass-1")

    def make_folder(self, name="provider"):
        folder = self.tmp_path / name
        folder.mkdir()
        return folder

    def connect(self, name="provider"):
        folder = self.make_folder(name)
        provider = connect_provider(self.user, LOCAL, name, {"path": str(folder)})
        return provider, folder


class CredentialTests(ManagerTestCase):
    def test_credentials_are_encrypted_at_rest_and_round_trip(self):
        folder = self.make_folder()
        provider = CloudProvider(user=self.user, provider_type=LOCAL, display_name="p")
        set_credentials(provider, {"path": str(folder), "secret": "hunter2"})
        provider.save()

        provider.refresh_from_db()
        self.assertNotIn("hunter2", provider.encrypted_credentials)
        self.assertNotIn(str(folder), provider.encrypted_credentials)
        self.assertEqual(get_credentials(provider), {"path": str(folder), "secret": "hunter2"})

    def test_missing_or_unreadable_credentials_raise_configuration_error(self):
        provider = CloudProvider(user=self.user, provider_type=LOCAL, display_name="p")
        with self.assertRaises(ProviderConfigurationError):
            get_credentials(provider)

        provider.encrypted_credentials = "garbage"
        with self.assertRaises(ProviderConfigurationError):
            get_credentials(provider)

    def test_unsupported_provider_type_raises_configuration_error(self):
        provider = CloudProvider(user=self.user, provider_type="carrier_pigeon", display_name="p")
        set_credentials(provider, {"path": "x"})
        with self.assertRaises(ProviderConfigurationError):
            get_adapter(provider)

    def test_missing_credential_field_raises_configuration_error(self):
        provider = CloudProvider(user=self.user, provider_type=LOCAL, display_name="p")
        set_credentials(provider, {})
        with self.assertRaises(ProviderConfigurationError):
            get_adapter(provider)


class ConnectTests(ManagerTestCase):
    def test_connect_stores_a_connected_provider_with_a_working_adapter(self):
        provider, folder = self.connect()

        provider.refresh_from_db()
        self.assertEqual(provider.user, self.user)
        self.assertEqual(provider.status, CloudProvider.Status.CONNECTED)
        self.assertIsNotNone(provider.last_checked_at)
        self.assertNotIn(str(folder), provider.encrypted_credentials)

        adapter = get_adapter(provider)
        self.assertIsInstance(adapter, LocalFolderAdapter)
        adapter.upload("a.bin", b"data")
        self.assertEqual(adapter.download("a.bin"), b"data")

    def test_connect_with_unreachable_provider_is_rejected_and_not_saved(self):
        missing = self.tmp_path / "does-not-exist"
        with self.assertRaises(ProviderUnavailableError):
            connect_provider(self.user, LOCAL, "broken", {"path": str(missing)})
        self.assertEqual(CloudProvider.objects.count(), 0)

    def test_a_user_can_connect_multiple_providers(self):
        for name in ("one", "two", "three"):
            self.connect(name)
        providers = self.user.cloud_providers.all()
        self.assertEqual(providers.count(), 3)
        self.assertTrue(all(p.status == CloudProvider.Status.CONNECTED for p in providers))


class CheckAndDisconnectTests(ManagerTestCase):
    def test_check_provider_tracks_outage_and_recovery(self):
        provider, folder = self.connect()

        shutil.rmtree(folder)
        self.assertFalse(check_provider(provider))
        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.ERROR)

        folder.mkdir()
        self.assertTrue(check_provider(provider))
        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.CONNECTED)

    def test_disconnect_forgets_credentials_but_keeps_the_record_and_storage_locations(self):
        provider, _ = self.connect()
        job = BackupJob.objects.create(user=self.user, name="job")
        file = File.objects.create(backup_job=job, original_path="/x")
        version = FileVersion.objects.create(
            file=file, version_number=1, original_filename="x", original_size=1
        )
        StorageLocation.objects.create(
            file_version=version, cloud_provider=provider, remote_path="x.part"
        )

        disconnect_provider(provider)

        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.DISCONNECTED)
        self.assertEqual(provider.encrypted_credentials, "")
        self.assertEqual(provider.storage_locations.count(), 1)
        with self.assertRaises(ProviderConfigurationError):
            get_adapter(provider)

    def test_check_provider_leaves_a_disconnected_provider_alone(self):
        provider, _ = self.connect()
        disconnect_provider(provider)
        self.assertFalse(check_provider(provider))
        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.DISCONNECTED)
