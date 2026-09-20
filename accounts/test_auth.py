from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from monitoring.models import ActivityLog

from .models import User

PASSWORD = "CorrectHorseBatteryStaple9"
NEW_PASSWORD = "Another-Correct-Horse-77"


class AuthTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="auth@example.com", password=PASSWORD, full_name="Auth User"
        )

    def login(self, password=PASSWORD, email="auth@example.com"):
        return APIClient().post(
            "/api/accounts/login/", {"email": email, "password": password}, format="json"
        )

    def call(self, method, path, token=None, **kwargs):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        return getattr(client, method)(path, format="json", **kwargs)

    def age(self, token_key, by):
        Token.objects.filter(key=token_key).update(created=timezone.now() - by)


class TokenExpiryTests(AuthTestCase):
    def test_a_fresh_token_works(self):
        token = self.login().data["token"]
        self.assertEqual(self.call("get", "/api/accounts/me/", token).status_code, 200)

    def test_a_token_is_refused_once_its_lifetime_has_passed_and_then_deleted(self):
        token = self.login().data["token"]
        self.age(token, settings.AUTH_TOKEN_LIFETIME + timedelta(minutes=1))

        response = self.call("get", "/api/accounts/me/", token)
        self.assertEqual(response.status_code, 401)
        self.assertIn("expired", response.data["detail"])
        self.assertFalse(Token.objects.filter(key=token).exists())

    def test_a_token_just_inside_its_lifetime_still_works(self):
        token = self.login().data["token"]
        self.age(token, settings.AUTH_TOKEN_LIFETIME - timedelta(minutes=1))
        self.assertEqual(self.call("get", "/api/accounts/me/", token).status_code, 200)

    @override_settings(AUTH_TOKEN_LIFETIME=timedelta(minutes=5))
    def test_the_lifetime_is_configurable(self):
        token = self.login().data["token"]
        self.age(token, timedelta(minutes=6))
        self.assertEqual(self.call("get", "/api/accounts/me/", token).status_code, 401)

    def test_logging_in_again_issues_a_new_token_and_ends_the_old_session(self):
        first = self.login().data["token"]
        second = self.login().data["token"]

        self.assertNotEqual(first, second)
        self.assertEqual(self.call("get", "/api/accounts/me/", first).status_code, 401)
        self.assertEqual(self.call("get", "/api/accounts/me/", second).status_code, 200)
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)

    def test_logging_in_after_expiry_gives_a_working_token(self):
        old = self.login().data["token"]
        self.age(old, settings.AUTH_TOKEN_LIFETIME + timedelta(days=1))

        fresh = self.login().data["token"]
        self.assertEqual(self.call("get", "/api/accounts/me/", fresh).status_code, 200)

    def test_another_users_session_is_not_affected(self):
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        other_token = self.login(email="other@example.com").data["token"]
        self.login()
        self.login()
        self.assertEqual(self.call("get", "/api/accounts/me/", other_token).status_code, 200)
        self.assertTrue(Token.objects.filter(user=other).exists())


class ChangePasswordTests(AuthTestCase):
    URL = "/api/accounts/change-password/"

    def payload(self, **overrides):
        return {
            "current_password": PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
            **overrides,
        }

    def test_it_requires_a_login(self):
        self.assertEqual(self.call("post", self.URL, data=self.payload()).status_code, 401)

    def test_success_changes_the_password_and_swaps_the_token(self):
        old_token = self.login().data["token"]
        response = self.call("post", self.URL, old_token, data=self.payload())

        self.assertEqual(response.status_code, 200)
        new_token = response.data["token"]
        self.assertNotEqual(new_token, old_token)
        self.assertEqual(self.call("get", "/api/accounts/me/", old_token).status_code, 401)
        self.assertEqual(self.call("get", "/api/accounts/me/", new_token).status_code, 200)

        self.assertEqual(self.login(PASSWORD).status_code, 401)
        self.assertEqual(self.login(NEW_PASSWORD).status_code, 200)

    def test_it_is_recorded_in_the_activity_log(self):
        token = self.login().data["token"]
        self.call("post", self.URL, token, data=self.payload())
        self.assertTrue(
            ActivityLog.objects.filter(user=self.user, action="password_changed").exists()
        )

    def test_a_wrong_current_password_is_refused_and_nothing_changes(self):
        token = self.login().data["token"]
        response = self.call("post", self.URL, token, data=self.payload(current_password="nope-nope-1"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("current password is incorrect", response.data["detail"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
        self.assertEqual(self.call("get", "/api/accounts/me/", token).status_code, 200)

    def test_mismatched_new_passwords_are_refused(self):
        token = self.login().data["token"]
        response = self.call("post", self.URL, token, data=self.payload(new_password2="Different-Pass-88"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("new_password2", response.data)

    def test_weak_passwords_are_refused_by_the_same_rules_as_registration(self):
        token = self.login().data["token"]
        for weak in ("password123", "short1", "12345678901234"):
            with self.subTest(password=weak):
                response = self.call(
                    "post", self.URL, token, data=self.payload(new_password1=weak, new_password2=weak)
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("new_password1", response.data)

    def test_a_password_too_similar_to_the_users_own_email_is_refused(self):
        token = self.login().data["token"]
        similar = "auth@example.com123"
        response = self.call(
            "post", self.URL, token, data=self.payload(new_password1=similar, new_password2=similar)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("similar", " ".join(response.data["new_password1"]))

    def test_other_users_sessions_survive(self):
        User.objects.create_user(email="other@example.com", password=PASSWORD)
        other_token = self.login(email="other@example.com").data["token"]
        token = self.login().data["token"]
        self.call("post", self.URL, token, data=self.payload())
        self.assertEqual(self.call("get", "/api/accounts/me/", other_token).status_code, 200)


class ProfileTests(AuthTestCase):
    def test_the_name_can_be_changed_and_persists(self):
        token = self.login().data["token"]
        response = self.call("patch", "/api/accounts/me/", token, data={"full_name": "New Name"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["full_name"], "New Name")
        self.assertEqual(self.call("get", "/api/accounts/me/", token).data["full_name"], "New Name")

    def test_the_email_cannot_be_changed_here(self):
        token = self.login().data["token"]
        self.call(
            "patch",
            "/api/accounts/me/",
            token,
            data={"full_name": "Same", "email": "hacker@example.com"},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "auth@example.com")

    def test_the_name_can_be_cleared_but_not_made_too_long(self):
        token = self.login().data["token"]
        self.assertEqual(
            self.call("patch", "/api/accounts/me/", token, data={"full_name": ""}).status_code, 200
        )
        self.assertEqual(
            self.call("patch", "/api/accounts/me/", token, data={"full_name": "x" * 151}).status_code,
            400,
        )

    def test_it_requires_a_login(self):
        self.assertEqual(self.call("patch", "/api/accounts/me/", data={"full_name": "x"}).status_code, 401)
