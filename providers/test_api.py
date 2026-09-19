import shutil
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User

from .models import CloudProvider


@override_settings(ALLOW_LOCAL_FOLDER_PROVIDERS=True)
class ProviderApiTestCase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.user = User.objects.create_user(email="api@example.com", password="irrelevant-pass-1")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def connect(self, name="provider", client=None, **overrides):
        folder = self.tmp_path / name
        folder.mkdir(exist_ok=True)
        payload = {
            "provider_type": "local_folder",
            "display_name": name,
            "credentials": {"path": str(folder)},
            **overrides,
        }
        return (client or self.client).post(reverse("providers:list"), payload, format="json"), folder


class ProviderApiTests(ProviderApiTestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(reverse("providers:list")).status_code, 401)
        self.assertEqual(anonymous.get(reverse("providers:types")).status_code, 401)

    def test_connect_returns_the_provider_without_credentials(self):
        response, folder = self.connect()

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["status"], "connected")
        self.assertNotIn("credentials", response.data)
        self.assertNotIn("encrypted_credentials", response.data)
        self.assertNotIn(str(folder), response.content.decode())

        stored = CloudProvider.objects.get(pk=response.data["id"])
        self.assertNotIn(str(folder), stored.encrypted_credentials)

    def test_list_only_shows_the_users_own_providers(self):
        self.connect("mine")
        other = User.objects.create_user(email="other@example.com", password="irrelevant-pass-1")
        other_client = APIClient()
        other_client.force_authenticate(other)
        self.connect("theirs", client=other_client)

        names = [p["display_name"] for p in self.client.get(reverse("providers:list")).data]
        self.assertEqual(names, ["mine"])

    def test_invalid_connections_are_rejected_and_not_saved(self):
        missing_folder = self.tmp_path / "nope"
        cases = {
            "missing path field": {"credentials": {}},
            "folder that does not exist": {"credentials": {"path": str(missing_folder)}},
            "unsupported type": {"provider_type": "carrier_pigeon"},
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                response, _ = self.connect("bad", **overrides)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CloudProvider.objects.count(), 0)

    def test_local_folder_providers_are_only_offered_when_enabled(self):
        types = [t["value"] for t in self.client.get(reverse("providers:types")).data]
        self.assertIn("local_folder", types)

        with override_settings(ALLOW_LOCAL_FOLDER_PROVIDERS=False):
            types = [t["value"] for t in self.client.get(reverse("providers:types")).data]
            self.assertNotIn("local_folder", types)
            response, _ = self.connect("blocked")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(CloudProvider.objects.count(), 0)

    def test_check_tracks_an_outage_and_recovery(self):
        response, folder = self.connect()
        url = reverse("providers:check", args=[response.data["id"]])

        shutil.rmtree(folder)
        self.assertEqual(self.client.post(url).data["status"], "error")
        folder.mkdir()
        self.assertEqual(self.client.post(url).data["status"], "connected")

    def test_disconnect_marks_the_provider_disconnected(self):
        response, _ = self.connect()
        url = reverse("providers:disconnect", args=[response.data["id"]])
        self.assertEqual(self.client.post(url).data["status"], "disconnected")
        self.assertEqual(CloudProvider.objects.get().encrypted_credentials, "")

    def test_other_users_providers_are_not_found(self):
        response, _ = self.connect()
        other = User.objects.create_user(email="other@example.com", password="irrelevant-pass-1")
        other_client = APIClient()
        other_client.force_authenticate(other)

        for name in ("providers:check", "providers:disconnect"):
            with self.subTest(name):
                url = reverse(name, args=[response.data["id"]])
                self.assertEqual(other_client.post(url).status_code, 404)
        self.assertEqual(CloudProvider.objects.get().status, "connected")
