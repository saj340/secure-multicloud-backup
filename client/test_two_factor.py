import base64
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

import pyotp
from PySide6.QtWidgets import QApplication, QDialog

from accounts import twofactor
from accounts.models import User

from .api_client import ApiClient, ApiError, TwoFactorRequired
from .test_client import EMAIL, PASSWORD, api_url
from .test_gui import GuiTestCase, wait_until
from .windows.login_window import LoginWindow
from .windows.settings_page import SettingsPage
from .windows.two_factor_dialogs import (
    QUIET_ZONE,
    BackupCodesDialog,
    BackupCodesView,
    ConfirmIdentityDialog,
    QrWidget,
    TurnOnDialog,
    qr_matrix,
)

START = 1_700_000_010.0
STEP = twofactor.STEP_SECONDS


class TwoFactorGuiCase(GuiTestCase):
    """A logged-in test user and a clock the test controls (the server runs in this process)."""

    def setUp(self):
        super().setUp()
        self.now = START
        patcher = mock.patch("accounts.twofactor._now", side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def code(self, secret, steps=0):
        return pyotp.TOTP(secret).at(self.now + steps * STEP)

    def advance(self):
        self.now += STEP

    def turn_on_via_api(self):
        """Enable two-factor for the test user; returns (secret, backup codes)."""
        secret = self.api.two_factor_setup(PASSWORD)["secret"]
        codes = self.api.two_factor_enable(self.code(secret))
        self.advance()
        return secret, codes

    def fresh_client(self):
        return ApiClient(api_url(self))

    def keep(self, widget):
        self.addCleanup(widget.deleteLater)
        return widget


class ApiClientTests(TwoFactorGuiCase):
    def test_a_password_only_login_to_a_protected_account_asks_for_a_code(self):
        self.turn_on_via_api()
        client = self.fresh_client()
        with self.assertRaises(TwoFactorRequired) as caught:
            client.login(EMAIL, PASSWORD)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIsInstance(caught.exception, ApiError)
        self.assertIsNone(client.token)

    def test_the_code_completes_the_login(self):
        secret, _ = self.turn_on_via_api()
        client = self.fresh_client()
        user = client.login(EMAIL, PASSWORD, self.code(secret))
        self.assertEqual(user["email"], EMAIL)
        self.assertTrue(user["two_factor_enabled"])
        self.assertTrue(client.token)

    def test_a_wrong_code_is_an_ordinary_error_not_a_request_for_a_code(self):
        self.turn_on_via_api()
        client = self.fresh_client()
        with self.assertRaises(ApiError) as caught:
            client.login(EMAIL, PASSWORD, "000000")
        self.assertNotIsInstance(caught.exception, TwoFactorRequired)
        self.assertEqual(caught.exception.message, "Invalid verification code.")

    def test_a_wrong_password_is_an_ordinary_error(self):
        self.turn_on_via_api()
        with self.assertRaises(ApiError) as caught:
            self.fresh_client().login(EMAIL, "wrong-password")
        self.assertNotIsInstance(caught.exception, TwoFactorRequired)
        self.assertEqual(caught.exception.message, "Invalid email or password.")

    def test_accounts_without_two_factor_log_in_as_before(self):
        self.assertEqual(self.fresh_client().login(EMAIL, PASSWORD)["email"], EMAIL)

    def test_setup_enable_disable_and_renew_through_the_client(self):
        setup = self.api.two_factor_setup(PASSWORD)
        self.assertTrue(setup["otpauth_uri"].startswith("otpauth://totp/"))
        codes = self.api.two_factor_enable(self.code(setup["secret"]))
        self.assertEqual(len(codes), 8)
        self.advance()
        renewed = self.api.renew_backup_codes(PASSWORD, self.code(setup["secret"]))
        self.assertFalse(set(codes) & set(renewed))
        self.advance()
        self.api.two_factor_disable(PASSWORD, self.code(setup["secret"]))
        self.assertFalse(self.api.me()["two_factor_enabled"])

    def test_wrong_passwords_and_codes_are_400s_so_they_never_look_like_an_expired_session(self):
        with self.assertRaises(ApiError) as caught:
            self.api.two_factor_setup("wrong-password")
        self.assertEqual(caught.exception.status_code, 400)
        self.api.two_factor_setup(PASSWORD)
        with self.assertRaises(ApiError) as caught:
            self.api.two_factor_enable("000000")
        self.assertEqual(caught.exception.status_code, 400)


class LoginWindowTests(TwoFactorGuiCase):
    def setUp(self):
        super().setUp()
        self.users = []
        self.window = self.keep(LoginWindow(self.fresh_client(), on_login_success=self.users.append))
        self.secret, self.backup_codes = self.turn_on_via_api()

    def submit(self, code=None):
        self.window.email_input.setText(EMAIL)
        self.window.password_input.setText(PASSWORD)
        if code is not None:
            self.window.code_input.setText(code)
        self.window.login_button.click()

    def test_the_code_step_is_hidden_until_it_is_needed(self):
        self.assertFalse(self.window.code_input.isVisibleTo(self.window))
        self.assertFalse(self.window.other_account_link.isVisibleTo(self.window))

    def test_a_correct_password_reveals_the_code_step_and_locks_the_credentials(self):
        self.submit()
        self.assertEqual(self.users, [])
        self.assertTrue(self.window.code_input.isVisibleTo(self.window))
        self.assertTrue(self.window.email_input.isReadOnly())
        self.assertTrue(self.window.password_input.isReadOnly())
        self.assertEqual(self.window.login_button.text(), "Verify and log in")
        self.assertIn("authenticator", self.window.status_label.text())

    def test_the_code_logs_in_and_the_window_goes_back_to_normal(self):
        self.submit()
        self.window.code_input.setText(self.code(self.secret))
        self.window.login_button.click()
        self.assertEqual([user["email"] for user in self.users], [EMAIL])
        self.assertEqual(self.window.password_input.text(), "")
        self.assertFalse(self.window.code_input.isVisibleTo(self.window))
        self.assertFalse(self.window.password_input.isReadOnly())
        self.assertEqual(self.window.login_button.text(), "Log in")

    def test_a_wrong_code_shows_the_error_clears_the_box_and_stays_on_the_step(self):
        self.submit()
        self.window.code_input.setText("000000")
        self.window.login_button.click()
        self.assertEqual(self.users, [])
        self.assertEqual(self.window.status_label.text(), "Invalid verification code.")
        self.assertEqual(self.window.code_input.text(), "")
        self.assertTrue(self.window.code_input.isVisibleTo(self.window))

    def test_a_backup_code_works_in_the_same_box(self):
        self.submit()
        self.window.code_input.setText(self.backup_codes[0])
        self.window.login_button.click()
        self.assertEqual(len(self.users), 1)

    def test_pressing_verify_with_an_empty_box_asks_for_the_code_without_calling_the_server(self):
        self.submit()
        with mock.patch.object(self.window.api_client, "login") as login:
            self.window.login_button.click()
        login.assert_not_called()
        self.assertIn("Enter the code", self.window.status_label.text())

    def test_use_a_different_account_goes_back_to_the_first_step(self):
        self.submit()
        self.window.other_account_link.click()
        self.assertFalse(self.window.code_input.isVisibleTo(self.window))
        self.assertFalse(self.window.email_input.isReadOnly())
        self.assertEqual(self.window.login_button.text(), "Log in")
        self.assertEqual(self.window.status_label.text(), "")

    def test_the_code_is_sent_with_the_login_not_before_the_password_is_accepted(self):
        calls = []
        real_login = self.window.api_client.login
        self.window.api_client.login = lambda *args: calls.append(args) or real_login(*args)
        self.submit()
        self.window.code_input.setText(self.code(self.secret))
        self.window.login_button.click()
        self.assertEqual(calls[0], (EMAIL, PASSWORD, None))
        self.assertEqual(calls[1], (EMAIL, PASSWORD, self.code(self.secret)))


class QrTests(TwoFactorGuiCase):
    URI = "otpauth://totp/Secure%20Multi-Cloud%20Backup:me%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=X"

    def test_the_matrix_is_square_and_starts_with_the_scanner_square(self):
        matrix = qr_matrix(self.URI)
        self.assertGreaterEqual(len(matrix), 21)
        self.assertTrue(all(len(row) == len(matrix) for row in matrix))
        self.assertEqual(matrix[0][:7], [True] * 7)
        self.assertEqual(matrix[1][:7], [True] + [False] * 5 + [True])
        self.assertEqual(matrix[2][:7], [True, False, True, True, True, False, True])

    def test_different_text_gives_a_different_code(self):
        self.assertNotEqual(qr_matrix(self.URI), qr_matrix(self.URI + "2"))

    def test_the_widget_draws_a_white_border_and_black_modules(self):
        widget = self.keep(QrWidget(size=240))
        widget.set_text(self.URI)
        image = widget.grab().toImage()
        modules = len(qr_matrix(self.URI)) + 2 * QUIET_ZONE
        cell = 240 / modules
        self.assertEqual(image.pixelColor(2, 2).name(), "#ffffff")  # inside the quiet zone
        first_module = int((QUIET_ZONE + 0.5) * cell)
        self.assertEqual(image.pixelColor(first_module, first_module).name(), "#000000")
        centre = int((QUIET_ZONE + 3.5) * cell)  # the middle of the scanner square is dark
        self.assertEqual(image.pixelColor(centre, centre).name(), "#000000")
        ring = int((QUIET_ZONE + 1.5) * cell)  # and it is surrounded by a light ring
        self.assertEqual(image.pixelColor(ring, ring).name(), "#ffffff")


class TurnOnDialogTests(TwoFactorGuiCase):
    def setUp(self):
        super().setUp()
        self.dialog = self.keep(TurnOnDialog(self.api))

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.dialog._busy))

    def to_scan_page(self):
        self.dialog.password_input.setText(PASSWORD)
        self.dialog.start_setup()
        self.assertTrue(wait_until(lambda: self.dialog.pages.currentIndex() == TurnOnDialog.SCAN_PAGE))
        return self.dialog.key_label.text().replace(" ", "")

    def test_it_starts_on_the_password_page(self):
        self.assertEqual(self.dialog.pages.currentIndex(), TurnOnDialog.PASSWORD_PAGE)

    def test_an_empty_password_is_refused_without_calling_the_server(self):
        with mock.patch.object(self.api, "two_factor_setup") as setup:
            self.dialog.start_setup()
        setup.assert_not_called()
        self.assertIn("password", self.dialog.message.text())

    def test_a_wrong_password_shows_the_server_message_and_stays_put(self):
        self.dialog.password_input.setText("wrong-password")
        self.dialog.start_setup()
        self.settle()
        self.assertEqual(self.dialog.pages.currentIndex(), TurnOnDialog.PASSWORD_PAGE)
        self.assertIn("incorrect", self.dialog.message.text())

    def test_the_right_password_shows_the_qr_code_and_the_key_for_typing(self):
        secret = self.to_scan_page()
        self.assertEqual(self.dialog.password_input.text(), "")
        self.assertTrue(self.dialog.qr._matrix)
        self.assertEqual(len(secret), 32)
        self.assertEqual(self.dialog.key_label.text().count(" "), 7)  # grouped in fours
        base64.b32decode(secret)  # a real base32 key, which is what authenticator apps expect

    def test_a_wrong_code_is_explained_and_the_box_is_cleared(self):
        self.to_scan_page()
        self.dialog.code_input.setText("000000")
        self.dialog.confirm_code()
        self.settle()
        self.assertEqual(self.dialog.pages.currentIndex(), TurnOnDialog.SCAN_PAGE)
        self.assertIn("not correct", self.dialog.message.text())
        self.assertEqual(self.dialog.code_input.text(), "")
        self.assertFalse(self.dialog.enabled)
        self.assertFalse(self.api.me()["two_factor_enabled"])

    def test_the_right_code_turns_it_on_and_shows_the_backup_codes(self):
        secret = self.to_scan_page()
        self.dialog.code_input.setText(self.code(secret))
        self.dialog.confirm_code()
        self.assertTrue(wait_until(lambda: self.dialog.pages.currentIndex() == TurnOnDialog.CODES_PAGE))
        self.assertTrue(self.dialog.enabled)
        self.assertEqual(len(self.dialog.codes_view.codes_text().splitlines()), 8)
        self.assertTrue(self.api.me()["two_factor_enabled"])

    def test_the_dialog_cannot_be_dismissed_until_the_codes_are_marked_as_saved(self):
        secret = self.to_scan_page()
        self.dialog.code_input.setText(self.code(secret))
        self.dialog.confirm_code()
        self.assertTrue(wait_until(lambda: self.dialog.pages.currentIndex() == TurnOnDialog.CODES_PAGE))
        self.dialog.show()
        self.assertFalse(self.dialog.done_button.isEnabled())
        self.dialog.reject()
        self.assertTrue(self.dialog.isVisible())
        self.assertIn("Save your backup codes", self.dialog.message.text())

        self.dialog.codes_view.saved_checkbox.setChecked(True)
        self.assertTrue(self.dialog.done_button.isEnabled())
        self.dialog.done_button.click()
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_cancelling_before_the_code_is_confirmed_leaves_two_factor_off(self):
        self.to_scan_page()
        self.dialog.show()
        self.dialog.reject()
        self.assertFalse(self.dialog.isVisible())
        self.assertFalse(self.dialog.enabled)
        self.assertFalse(self.api.me()["two_factor_enabled"])


class BackupCodesTests(TwoFactorGuiCase):
    CODES = ["AAAA-BBBB-CCCC-DDDD", "EEEE-FFFF-GGGG-HHHH"]

    def test_copy_puts_all_the_codes_on_the_clipboard(self):
        view = self.keep(BackupCodesView())
        view.set_codes(self.CODES)
        QApplication.clipboard().setText("")
        view.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), "\n".join(self.CODES))

    def test_save_writes_the_codes_to_the_chosen_file(self):
        view = self.keep(BackupCodesView())
        view.set_codes(self.CODES)
        target = self.tmp / "codes.txt"
        with mock.patch(
            "client.windows.two_factor_dialogs.QFileDialog.getSaveFileName",
            return_value=(str(target), "Text files (*.txt)"),
        ):
            view.save_button.click()
        text = target.read_text(encoding="utf-8")
        for code in self.CODES:
            self.assertIn(code, text)

    def test_cancelling_the_save_dialog_writes_nothing(self):
        view = self.keep(BackupCodesView())
        view.set_codes(self.CODES)
        with mock.patch(
            "client.windows.two_factor_dialogs.QFileDialog.getSaveFileName", return_value=("", "")
        ):
            view.save_button.click()
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_new_codes_start_unticked(self):
        view = self.keep(BackupCodesView())
        view.set_codes(self.CODES)
        view.saved_checkbox.setChecked(True)
        view.set_codes(self.CODES)
        self.assertFalse(view.saved_checkbox.isChecked())

    def test_the_renewal_dialog_stays_open_until_the_codes_are_marked_as_saved(self):
        dialog = self.keep(BackupCodesDialog(self.CODES))
        dialog.show()
        self.assertFalse(dialog.done_button.isEnabled())
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        dialog.view.saved_checkbox.setChecked(True)
        dialog.reject()
        self.assertFalse(dialog.isVisible())


class ConfirmIdentityDialogTests(TwoFactorGuiCase):
    def make(self):
        return self.keep(ConfirmIdentityDialog("Turn off", "Please confirm."))

    def test_both_fields_are_needed(self):
        dialog = self.make()
        dialog.show()
        dialog.password_input.setText(PASSWORD)
        dialog._accept_if_filled()
        self.assertTrue(dialog.isVisible())
        self.assertIn("both", dialog.error_label.text())
        dialog.code_input.setText("123456")
        dialog._accept_if_filled()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_values_are_what_was_typed_with_the_code_trimmed(self):
        dialog = self.make()
        dialog.password_input.setText(" pass word ")
        dialog.code_input.setText("  123456 ")
        self.assertEqual(dialog.values(), (" pass word ", "123456"))


class SettingsPageTwoFactorTests(TwoFactorGuiCase):
    def setUp(self):
        super().setUp()
        self.page = self.keep(SettingsPage(self.api))

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def show_state(self):
        self.page.set_user(self.api.me())

    def test_when_off_it_says_so_and_only_turn_on_is_available(self):
        self.show_state()
        self.assertTrue(self.page.two_factor_status.text().startswith("Off"))
        self.assertTrue(self.page.turn_on_button.isEnabled())
        self.assertFalse(self.page.turn_off_button.isEnabled())
        self.assertFalse(self.page.new_codes_button.isEnabled())

    def test_when_on_it_says_so_with_the_codes_left_and_only_the_other_two_are_available(self):
        self.turn_on_via_api()
        self.show_state()
        self.assertTrue(self.page.two_factor_status.text().startswith("On"))
        self.assertIn("Backup codes left: 8", self.page.two_factor_status.text())
        self.assertFalse(self.page.turn_on_button.isEnabled())
        self.assertTrue(self.page.turn_off_button.isEnabled())
        self.assertTrue(self.page.new_codes_button.isEnabled())

    def test_the_buttons_are_disabled_while_something_is_in_progress(self):
        self.turn_on_via_api()
        self.show_state()
        self.page._busy = True
        self.page._update_buttons()
        for button in (self.page.turn_on_button, self.page.turn_off_button, self.page.new_codes_button):
            self.assertFalse(button.isEnabled())

    def test_turn_on_opens_the_dialog_and_shows_the_new_state_afterwards(self):
        def fake_exec(dialog):
            secret = self.api.two_factor_setup(PASSWORD)["secret"]
            self.api.two_factor_enable(self.code(secret))
            dialog.enabled = True
            return QDialog.DialogCode.Accepted

        with mock.patch.object(TurnOnDialog, "exec", fake_exec):
            self.page.turn_on_two_factor()
        self.assertEqual(self.page.two_factor_message.text(), "Two-factor login is on.")
        self.assertTrue(wait_until(lambda: self.page.two_factor_status.text().startswith("On")))

    def test_turn_off_asks_for_the_password_and_a_code_then_turns_it_off(self):
        secret, _ = self.turn_on_via_api()
        self.show_state()
        with mock.patch.object(ConfirmIdentityDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                mock.patch.object(ConfirmIdentityDialog, "values", return_value=(PASSWORD, self.code(secret))):
            self.page.turn_off_two_factor()
            self.settle()
        self.assertEqual(self.page.two_factor_message.text(), "Two-factor login is off.")
        self.assertFalse(self.api.me()["two_factor_enabled"])
        self.assertTrue(wait_until(lambda: self.page.two_factor_status.text().startswith("Off")))

    def test_cancelling_the_confirmation_changes_nothing(self):
        self.turn_on_via_api()
        self.show_state()
        with mock.patch.object(ConfirmIdentityDialog, "exec", return_value=QDialog.DialogCode.Rejected), \
                mock.patch.object(self.api, "two_factor_disable") as disable:
            self.page.turn_off_two_factor()
            self.settle()  # any request the page started has run its course
        disable.assert_not_called()
        self.assertEqual(self.page.two_factor_message.text(), "")
        self.assertTrue(self.api.me()["two_factor_enabled"])

    def test_closing_the_turn_on_dialog_without_finishing_says_nothing_and_changes_nothing(self):
        self.show_state()
        with mock.patch.object(TurnOnDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.page.turn_on_two_factor()
        self.assertEqual(self.page.two_factor_message.text(), "")
        self.assertTrue(self.page.turn_on_button.isEnabled())
        self.assertFalse(self.api.me()["two_factor_enabled"])

    def test_a_wrong_code_when_turning_off_is_reported_and_it_stays_on(self):
        self.turn_on_via_api()
        self.show_state()
        with mock.patch.object(ConfirmIdentityDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                mock.patch.object(ConfirmIdentityDialog, "values", return_value=(PASSWORD, "000000")):
            self.page.turn_off_two_factor()
            self.settle()
        self.assertIn("not correct", self.page.two_factor_message.text())
        self.assertTrue(self.api.me()["two_factor_enabled"])

    def test_new_backup_codes_are_shown_and_the_old_ones_stop_working(self):
        secret, old = self.turn_on_via_api()
        self.show_state()
        shown = []
        with mock.patch.object(ConfirmIdentityDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                mock.patch.object(ConfirmIdentityDialog, "values", return_value=(PASSWORD, self.code(secret))), \
                mock.patch.object(BackupCodesDialog, "exec", lambda dialog: shown.append(dialog.view.codes_text())):
            self.page.renew_backup_codes()
            self.assertTrue(wait_until(lambda: shown))
        new_codes = shown[0].splitlines()
        self.assertEqual(len(new_codes), 8)
        self.assertFalse(set(new_codes) & set(old))
        self.assertIn("New backup codes created.", self.page.two_factor_message.text())
        with self.assertRaises(ApiError):
            self.fresh_client().login(EMAIL, PASSWORD, old[0])
        self.assertEqual(self.fresh_client().login(EMAIL, PASSWORD, new_codes[0])["email"], EMAIL)


class ActivityLabelTests(TwoFactorGuiCase):
    def test_the_new_events_have_readable_labels(self):
        from .windows.activity_page import describe_event

        self.assertEqual(describe_event("two_factor_enabled"), "Two-factor login turned on")
        self.assertEqual(describe_event("two_factor_disabled"), "Two-factor login turned off")
        self.assertEqual(describe_event("backup_codes_renewed"), "New backup codes created")
        self.assertEqual(describe_event("backup_code_used"), "Signed in with a backup code")
