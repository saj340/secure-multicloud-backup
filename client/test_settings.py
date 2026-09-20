import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token

from accounts.models import User

from .api_client import ApiClient, ApiError
from .main import AppWindows
from .test_client import EMAIL, PASSWORD, api_url
from .test_gui import GuiTestCase, wait_until
from .windows.main_window import MainWindow
from .windows.settings_page import SettingsPage
from .workers import run_in_background, set_unauthorized_handler

NEW_PASSWORD = "Another-Correct-Horse-77"


class SettingsPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.changed = []
        self.page = SettingsPage(self.api, on_profile_changed=self.changed.append)
        self.addCleanup(self.page.deleteLater)
        self.page.set_user(self.api.me())

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def attempt_password_change(self, current, new, confirm=None):
        self.page.current_input.setText(current)
        self.page.new_input.setText(new)
        self.page.confirm_input.setText(new if confirm is None else confirm)
        self.page.change_password()
        self.settle()

    def test_it_shows_the_email_and_the_name(self):
        self.assertEqual(self.page.email_label.text(), EMAIL)
        self.assertEqual(self.page.name_input.text(), "Gui Test")

    def test_saving_a_new_name_updates_the_server_and_reports_it(self):
        self.page.name_input.setText("Renamed Person")
        self.page.save_profile()
        self.settle()

        self.assertEqual(self.page.profile_message.text(), "Saved.")
        self.assertEqual(self.api.me()["full_name"], "Renamed Person")
        self.assertEqual(self.changed[-1]["full_name"], "Renamed Person")

    def test_the_name_can_be_cleared(self):
        self.page.name_input.setText("")
        self.page.save_profile()
        self.settle()
        self.assertEqual(self.api.me()["full_name"], "")

    def test_changing_the_password_keeps_this_session_and_kills_the_old_token(self):
        old_token = self.api.token
        self.attempt_password_change(PASSWORD, NEW_PASSWORD)

        self.assertIn("Password changed", self.page.password_message.text())
        self.assertNotEqual(self.api.token, old_token)
        self.assertEqual(self.api.me()["email"], EMAIL)  # this session carries on
        self.assertEqual(
            (self.page.current_input.text(), self.page.new_input.text(), self.page.confirm_input.text()),
            ("", "", ""),
        )

        stale = ApiClient(api_url(self))
        stale.token = old_token
        with self.assertRaises(ApiError) as caught:
            stale.me()
        self.assertEqual(caught.exception.status_code, 401)

    def test_the_new_password_works_and_the_old_one_does_not(self):
        self.attempt_password_change(PASSWORD, NEW_PASSWORD)

        with self.assertRaises(ApiError):
            ApiClient(api_url(self)).login(EMAIL, PASSWORD)
        self.assertEqual(ApiClient(api_url(self)).login(EMAIL, NEW_PASSWORD)["email"], EMAIL)

    def test_a_wrong_current_password_is_refused_and_the_session_stays(self):
        token = self.api.token
        self.attempt_password_change("not-my-password-1", NEW_PASSWORD)

        self.assertIn("current password is incorrect", self.page.password_message.text())
        self.assertEqual(self.api.token, token)
        self.assertEqual(self.api.me()["email"], EMAIL)

    def test_mismatched_passwords_are_caught_before_anything_is_sent(self):
        token = self.api.token
        self.attempt_password_change(PASSWORD, NEW_PASSWORD, confirm="Something-Else-99")

        self.assertIn("don't match", self.page.password_message.text())
        self.assertEqual(self.api.token, token)
        self.assertTrue(User.objects.get(email=EMAIL).check_password(PASSWORD))

    def test_empty_fields_are_flagged(self):
        self.attempt_password_change(PASSWORD, "", confirm="")
        self.assertIn("all three", self.page.password_message.text())

    def test_weak_passwords_are_refused_with_the_reason_in_plain_words(self):
        self.attempt_password_change(PASSWORD, "password123")
        self.assertIn("New password:", self.page.password_message.text())
        self.assertIn("too common", self.page.password_message.text())

        self.attempt_password_change(PASSWORD, EMAIL + "1")
        self.assertIn("too similar", self.page.password_message.text())
        self.assertTrue(User.objects.get(email=EMAIL).check_password(PASSWORD))


class MainWindowSettingsTests(GuiTestCase):
    def test_saving_a_name_updates_the_welcome_message_without_leaving_settings(self):
        window = MainWindow(self.api)
        self.addCleanup(window.deleteLater)
        window.set_user(self.api.me())
        window.sidebar.setCurrentRow(6)
        self.assertEqual(window.sidebar.item(6).text(), "Settings")

        window.settings_page.name_input.setText("Display Name Changed")
        window.settings_page.save_profile()
        self.assertTrue(wait_until(lambda: "Display Name Changed" in window.dashboard.welcome_label.text()))
        self.assertEqual(window.pages.currentIndex(), 6)


class SessionExpiryTests(GuiTestCase):
    def open_app(self):
        api = ApiClient(api_url(self))
        windows = AppWindows(api)
        self.addCleanup(set_unauthorized_handler, None)
        self.addCleanup(lambda: [w.close() for w in windows._windows])
        windows.start()
        windows.login_window.email_input.setText(EMAIL)
        windows.login_window.password_input.setText(PASSWORD)
        windows.login_window.login_button.click()
        self.assertTrue(windows.main_window.isVisible())
        return api, windows

    def test_an_expired_session_sends_the_user_to_login_with_a_notice(self):
        api, windows = self.open_app()
        # Age the token past its lifetime, as if the app had been left open for days.
        Token.objects.update(created=timezone.now() - timedelta(days=30))

        windows.main_window.sidebar.setCurrentRow(2)  # Backup History makes a request
        self.assertTrue(wait_until(lambda: windows.login_window.isVisible()))

        self.assertFalse(windows.main_window.isVisible())
        self.assertIn("session has expired", windows.login_window.status_label.text())
        self.assertIsNone(api.token)

    def test_logging_out_with_an_already_dead_session_is_not_reported_as_expiry(self):
        api, windows = self.open_app()
        # Let the dashboard's initial request finish first, or it would (rightly) hit the 401.
        self.assertTrue(wait_until(lambda: windows.main_window.dashboard.providers_value.text() != "–"))
        Token.objects.all().delete()

        windows.main_window.logout_button.click()
        self.assertTrue(wait_until(lambda: windows.login_window.isVisible()))
        self.assertEqual(windows.login_window.status_label.text(), "")
        self.assertIsNone(api.token)


class UnauthorizedHandlerTests(SimpleTestCase):
    def test_a_401_calls_the_handler_and_still_the_normal_error_callback(self):
        calls = []
        set_unauthorized_handler(lambda: calls.append("handler"))
        self.addCleanup(set_unauthorized_handler, None)

        def work():
            raise ApiError("Your session has expired.", 401)

        run_in_background(work, on_error=lambda message: calls.append(("error", message)))
        self.assertTrue(wait_until(lambda: len(calls) == 2))
        self.assertEqual(calls, ["handler", ("error", "Your session has expired.")])

    def test_other_failures_do_not_call_the_handler(self):
        calls = []
        set_unauthorized_handler(lambda: calls.append("handler"))
        self.addCleanup(set_unauthorized_handler, None)

        def work():
            raise ApiError("Bad request", 400)

        run_in_background(work, on_error=lambda message: calls.append(("error", message)))
        self.assertTrue(wait_until(lambda: len(calls) == 1))
        self.assertEqual(calls, [("error", "Bad request")])
