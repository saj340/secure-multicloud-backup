import tempfile
from pathlib import Path

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from providers.manager import check_provider, connect_provider, disconnect_provider
from providers.models import CloudProvider

from .models import ActivityLog

PASSWORD = "CorrectHorseBatteryStaple9"
LOCAL = CloudProvider.ProviderType.LOCAL_FOLDER


def actions(**filters):
    return list(ActivityLog.objects.filter(**filters).order_by("id").values_list("action", flat=True))


class AccountEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="log@example.com", password=PASSWORD)

    def login(self, email="log@example.com", password=PASSWORD):
        return APIClient().post(
            "/api/accounts/login/", {"email": email, "password": password}, format="json"
        )

    def test_registering_is_logged(self):
        APIClient().post(
            "/api/accounts/register/",
            {
                "email": "new@example.com",
                "full_name": "New Person",
                "password1": "Fresh-Correct-Horse-55",
                "password2": "Fresh-Correct-Horse-55",
            },
            format="json",
        )
        self.assertEqual(actions(user__email="new@example.com"), ["registered"])

    def test_a_successful_login_and_logout_are_logged(self):
        token = self.login().data["token"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        client.post("/api/accounts/logout/")
        self.assertEqual(actions(user=self.user), ["login", "logout"])

    def test_a_failed_login_is_a_warning_and_never_stores_the_password(self):
        self.login(password="wrong-password-123")
        entry = ActivityLog.objects.get()
        self.assertEqual(entry.action, "login_failed")
        self.assertEqual(entry.level, ActivityLog.Level.WARNING)
        self.assertEqual(entry.user, self.user)
        self.assertNotIn("wrong-password-123", str(entry.metadata) + entry.description)

    def test_a_failed_login_for_an_unknown_email_is_logged_the_same_way(self):
        response = self.login(email="nobody@example.com")
        self.assertEqual(response.status_code, 401)
        entry = ActivityLog.objects.get()
        self.assertEqual((entry.action, entry.level), ("login_failed", ActivityLog.Level.WARNING))
        self.assertIsNone(entry.user)

    def test_a_blocked_login_attempt_is_not_logged_as_a_failure_each_time(self):
        for _ in range(8):
            self.login(password="wrong-password-123")
        # 5 wrong guesses are allowed; the rest are refused before being checked.
        self.assertEqual(len(actions(action="login_failed")), 5)


class ProviderEventTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.user = User.objects.create_user(email="prov@example.com", password=PASSWORD)
        self.folder = self.root / "cloud"
        self.folder.mkdir()
        self.provider = connect_provider(self.user, LOCAL, "My Cloud", {"path": str(self.folder)})

    def test_connecting_is_logged_without_the_credentials(self):
        entry = ActivityLog.objects.get(action="provider_connected")
        self.assertEqual(entry.user, self.user)
        self.assertIn("My Cloud", entry.description)
        self.assertNotIn(str(self.folder), entry.description + str(entry.metadata))

    def test_only_a_change_of_health_is_logged(self):
        check_provider(self.provider)  # healthy and stays healthy
        self.assertEqual(actions(action__startswith="provider_"), ["provider_connected"])

        moved = self.root / "moved"
        self.folder.rename(moved)
        check_provider(self.provider)
        check_provider(self.provider)  # still down: no second entry
        self.assertEqual(
            actions(action__startswith="provider_"), ["provider_connected", "provider_unreachable"]
        )
        self.assertEqual(
            ActivityLog.objects.get(action="provider_unreachable").level, ActivityLog.Level.WARNING
        )

        moved.rename(self.folder)
        check_provider(self.provider)
        self.assertEqual(actions(action__startswith="provider_")[-1], "provider_recovered")

    def test_disconnecting_is_logged(self):
        disconnect_provider(self.provider)
        self.assertEqual(
            actions(action__startswith="provider_"), ["provider_connected", "provider_disconnected"]
        )


class ActivityApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="me@example.com", password=PASSWORD)
        self.other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def add(self, user, count, action="backup_run_started"):
        for number in range(count):
            ActivityLog.objects.create(user=user, action=action, description=f"entry {number}")

    def test_it_requires_a_login(self):
        response = APIClient().get("/api/monitoring/activity/")
        self.assertEqual(response.status_code, 401)

    def test_it_lists_only_the_callers_entries_newest_first(self):
        self.add(self.user, 3)
        self.add(self.other, 2, action="secret_event")
        rows = self.client.get("/api/monitoring/activity/").data
        self.assertEqual([row["description"] for row in rows], ["entry 2", "entry 1", "entry 0"])
        self.assertNotIn("secret_event", str(rows))

    def test_it_never_exposes_metadata_such_as_ip_addresses(self):
        ActivityLog.objects.create(user=self.user, action="login_failed", metadata={"ip": "10.9.8.7"})
        rows = self.client.get("/api/monitoring/activity/").data
        self.assertNotIn("10.9.8.7", str(rows))
        self.assertEqual(set(rows[0]), {"id", "action", "description", "level", "created_at"})

    def test_the_limit_is_honoured_and_capped(self):
        self.add(self.user, 5)
        self.assertEqual(len(self.client.get("/api/monitoring/activity/?limit=2").data), 2)
        self.assertEqual(len(self.client.get("/api/monitoring/activity/?limit=0").data), 1)
        self.assertEqual(len(self.client.get("/api/monitoring/activity/?limit=abc").data), 5)
        self.assertEqual(len(self.client.get("/api/monitoring/activity/?limit=99999").data), 5)
