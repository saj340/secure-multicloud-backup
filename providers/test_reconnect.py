from rest_framework.test import APIClient

from accounts.models import User
from backups import test_manager
from backups.manager import RecoveryError
from monitoring.models import ActivityLog

from .adapters.base import ProviderConfigurationError, ProviderUnavailableError
from .manager import disconnect_provider, get_credentials, reconnect_provider
from .models import CloudProvider


class ReconnectTests(test_manager.PipelineTestCase):
    def reconnect(self, provider, folder):
        return reconnect_provider(provider, {"path": str(folder)})

    def test_it_reuses_the_same_record_and_restores_it(self):
        provider = self.providers[0]
        disconnect_provider(provider)
        self.assertEqual(provider.encrypted_credentials, "")

        result, _checked, _missing = self.reconnect(provider, self.folders[0])

        self.assertEqual(result.pk, provider.pk)
        self.assertEqual(CloudProvider.objects.filter(user=self.user).count(), 3)
        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.CONNECTED)
        self.assertEqual(get_credentials(provider), {"path": str(self.folders[0])})

    def test_a_backup_made_before_the_disconnect_is_recoverable_again_after_reconnecting(self):
        version = self.client_backup()
        disconnect_provider(self.providers[0])
        disconnect_provider(self.providers[1])
        # Two of three copies out of reach: more than the parity can cover.
        with self.assertRaises(RecoveryError):
            self.client_recover(version)

        self.reconnect(self.providers[0], self.folders[0])

        self.assertEqual(self.client_recover(version)[0], test_manager.PLAINTEXT)

    def test_it_counts_the_stored_fragments_it_can_find(self):
        self.client_backup()
        disconnect_provider(self.providers[0])
        _provider, checked, missing = self.reconnect(self.providers[0], self.folders[0])
        self.assertEqual((checked, missing), (1, 0))
        self.assertEqual(ActivityLog.objects.get(action="provider_reconnected").level, "info")

    def test_credentials_for_a_different_place_are_reported_as_missing_fragments(self):
        self.client_backup()
        elsewhere = self.tmp_path / "somewhere-else"
        elsewhere.mkdir()
        disconnect_provider(self.providers[0])

        _provider, checked, missing = self.reconnect(self.providers[0], elsewhere)

        self.assertEqual((checked, missing), (1, 1))
        entry = ActivityLog.objects.get(action="provider_reconnected")
        self.assertEqual(entry.level, ActivityLog.Level.WARNING)
        self.assertIn("1 of 1", entry.description)

    def test_only_a_limited_sample_of_fragments_is_checked(self):
        from unittest import mock

        for _ in range(4):
            self.client_backup()
        disconnect_provider(self.providers[0])
        with mock.patch("providers.manager.RECONNECT_SAMPLE", 2):
            _provider, checked, _missing = self.reconnect(self.providers[0], self.folders[0])
        self.assertEqual(checked, 2)

    def test_a_provider_that_is_already_connected_cannot_be_reconnected(self):
        with self.assertRaises(ProviderConfigurationError):
            self.reconnect(self.providers[0], self.folders[0])

    def test_unreachable_credentials_change_nothing(self):
        provider = self.providers[0]
        disconnect_provider(provider)
        with self.assertRaises(ProviderUnavailableError):
            self.reconnect(provider, self.tmp_path / "does-not-exist")
        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.DISCONNECTED)
        self.assertEqual(provider.encrypted_credentials, "")

    def test_a_provider_in_error_can_be_given_new_credentials(self):
        provider = self.providers[0]
        provider.status = CloudProvider.Status.ERROR
        provider.save()
        self.reconnect(provider, self.folders[0])
        provider.refresh_from_db()
        self.assertEqual(provider.status, CloudProvider.Status.CONNECTED)


class ReconnectApiTests(test_manager.PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.url = f"/api/providers/{self.providers[0].pk}/reconnect/"

    def disconnect_first_provider(self):
        disconnect_provider(self.providers[0])

    def test_it_reconnects_and_reports_fragment_counts_without_echoing_credentials(self):
        self.client_backup()  # so the provider holds a fragment before it is disconnected
        self.disconnect_first_provider()
        response = self.api.post(
            self.url, {"credentials": {"path": str(self.folders[0])}}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "connected")
        self.assertEqual((response.data["fragments_checked"], response.data["fragments_missing"]), (1, 0))
        self.assertNotIn(str(self.folders[0]), str(response.data))

    def test_it_needs_credentials(self):
        self.disconnect_first_provider()
        self.assertEqual(self.api.post(self.url, {}, format="json").status_code, 400)

    def test_bad_credentials_are_a_400_with_a_message(self):
        self.disconnect_first_provider()
        response = self.api.post(
            self.url, {"credentials": {"path": str(self.tmp_path / "nope")}}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not reach", response.data["detail"])

    def test_another_users_provider_is_a_404(self):
        self.disconnect_first_provider()
        stranger = APIClient()
        stranger.force_authenticate(User.objects.create_user(email="x@example.com", password="pw-Strong-123"))
        response = stranger.post(
            self.url, {"credentials": {"path": str(self.folders[0])}}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.providers[0].refresh_from_db()
        self.assertEqual(self.providers[0].status, CloudProvider.Status.DISCONNECTED)

    def test_it_requires_a_login(self):
        response = APIClient().post(self.url, {"credentials": {}}, format="json")
        self.assertEqual(response.status_code, 401)
