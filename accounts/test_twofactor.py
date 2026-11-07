import re
from unittest import mock

import pyotp
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from monitoring.models import ActivityLog
from protection.crypto import decrypt_with_master_key

from . import twofactor
from .models import BackupCode, User

PASSWORD = "CorrectHorseBatteryStaple9"
START = 1_700_000_010.0  # an arbitrary fixed moment; tests move the clock explicitly
STEP = twofactor.STEP_SECONDS
BACKUP_FORMAT = re.compile(r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")


class TwoFactorTestCase(TestCase):
    def setUp(self):
        self.now = START
        patcher = mock.patch("accounts.twofactor._now", side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(email="two@example.com", password=PASSWORD)

    # ------------------------------------------------------------- helpers
    def post(self, path, data=None, token=None, ip="10.0.0.1"):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        return client.post(f"/api/accounts/{path}", data or {}, format="json", REMOTE_ADDR=ip)

    def login(self, code=None, password=PASSWORD, ip="10.0.0.1"):
        data = {"email": "two@example.com", "password": password}
        if code is not None:
            data["code"] = code
        return self.post("login/", data, ip=ip)

    def code(self, secret, steps=0):
        """The authenticator app's code for the current moment, `steps` periods away."""
        return pyotp.TOTP(secret).at(self.now + steps * STEP)

    def advance(self, steps=1):
        self.now += steps * STEP

    def enrol(self):
        """Turn two-factor on for the user; returns (secret, backup codes, session token)."""
        token = self.login().data["token"]
        secret = self.post("two-factor/setup/", {"password": PASSWORD}, token).data["secret"]
        enabled = self.post("two-factor/enable/", {"code": self.code(secret)}, token)
        self.assertEqual(enabled.status_code, 200)
        self.advance()  # the enrolment code is spent; the next login needs a fresh one
        return secret, enabled.data["backup_codes"], token

    def actions(self):
        return list(ActivityLog.objects.values_list("action", "level"))


class EnrolmentTests(TwoFactorTestCase):
    def test_setup_needs_a_session(self):
        self.assertEqual(self.post("two-factor/setup/", {"password": PASSWORD}).status_code, 401)

    def test_setup_needs_the_password(self):
        token = self.login().data["token"]
        response = self.post("two-factor/setup/", {"password": "wrong"}, token)
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.totp_secret, "")

    def test_setup_gives_a_secret_and_an_authenticator_link(self):
        token = self.login().data["token"]
        data = self.post("two-factor/setup/", {"password": PASSWORD}, token).data
        uri = data["otpauth_uri"]
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn(f"secret={data['secret']}", uri)
        self.assertIn("two%40example.com", uri)
        self.assertIn("issuer=Secure%20Multi-Cloud%20Backup", uri)

    def test_the_secret_is_stored_encrypted_and_never_returned_again(self):
        token = self.login().data["token"]
        secret = self.post("two-factor/setup/", {"password": PASSWORD}, token).data["secret"]
        self.user.refresh_from_db()
        self.assertNotIn(secret, self.user.totp_secret)
        self.assertEqual(decrypt_with_master_key(self.user.totp_secret).decode(), secret)
        for path in ("me/",):
            response = APIClient()
            response.credentials(HTTP_AUTHORIZATION=f"Token {token}")
            self.assertNotIn(secret, response.get(f"/api/accounts/{path}").content.decode())

    def test_it_is_not_on_until_a_code_from_the_app_is_confirmed(self):
        token = self.login().data["token"]
        self.post("two-factor/setup/", {"password": PASSWORD}, token)
        self.assertNotIn("two_factor_required", self.login().data)
        self.assertEqual(self.login().status_code, 200)

    def test_a_wrong_code_does_not_turn_it_on(self):
        token = self.login().data["token"]
        secret = self.post("two-factor/setup/", {"password": PASSWORD}, token).data["secret"]
        response = self.post("two-factor/enable/", {"code": self.code(secret, steps=-5)}, token)
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)

    def test_enable_without_setup_is_refused_and_says_why(self):
        token = self.login().data["token"]
        response = self.post("two-factor/enable/", {"code": "123456"}, token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Start the setup first.")

    def test_a_right_code_turns_it_on_and_gives_eight_backup_codes_once(self):
        secret, codes, token = self.enrol()
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)
        self.assertEqual(len(codes), 8)
        self.assertEqual(len(set(codes)), 8)
        self.assertTrue(all(BACKUP_FORMAT.match(code) for code in codes))

    def test_backup_codes_are_stored_only_as_hashes(self):
        _secret, codes, _token = self.enrol()
        stored = "".join(BackupCode.objects.values_list("code_hash", flat=True))
        for code in codes:
            self.assertNotIn(code.replace("-", ""), stored)
        self.assertEqual(BackupCode.objects.count(), 8)

    def test_setup_is_refused_while_it_is_already_on(self):
        _secret, _codes, _token = self.enrol()
        token = self.login(code=self.code(self.secret_of_user())).data["token"]
        self.assertEqual(self.post("two-factor/setup/", {"password": PASSWORD}, token).status_code, 400)

    def secret_of_user(self):
        self.user.refresh_from_db()
        return decrypt_with_master_key(self.user.totp_secret).decode()

    def test_starting_setup_again_replaces_the_pending_secret(self):
        token = self.login().data["token"]
        first = self.post("two-factor/setup/", {"password": PASSWORD}, token).data["secret"]
        second = self.post("two-factor/setup/", {"password": PASSWORD}, token).data["secret"]
        self.assertNotEqual(first, second)
        response = self.post("two-factor/enable/", {"code": self.code(first)}, token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post("two-factor/enable/", {"code": self.code(second)}, token).status_code, 200)

    def test_me_reports_the_state_and_how_many_backup_codes_are_left(self):
        _secret, _codes, _token = self.enrol()
        token = self.login(code=self.code(self.secret_of_user())).data["token"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        data = client.get("/api/accounts/me/").data
        self.assertTrue(data["two_factor_enabled"])
        self.assertEqual(data["backup_codes_left"], 8)

    def test_enabling_is_recorded_in_the_activity_log(self):
        self.enrol()
        self.assertIn(("two_factor_enabled", "info"), self.actions())

    def test_repeated_wrong_passwords_or_codes_are_rate_limited(self):
        token = self.login().data["token"]
        for _ in range(5):
            self.post("two-factor/setup/", {"password": "wrong"}, token)
        response = self.post("two-factor/setup/", {"password": PASSWORD}, token)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)


class LoginTests(TwoFactorTestCase):
    def test_accounts_without_two_factor_log_in_exactly_as_before(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.data)

    def test_the_password_alone_is_not_enough_and_no_session_is_created(self):
        self.enrol()
        Token.objects.all().delete()
        response = self.login()
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.data["two_factor_required"])
        self.assertNotIn("token", response.data)
        self.assertEqual(Token.objects.count(), 0)

    def test_a_wrong_password_never_reveals_that_two_factor_is_on(self):
        self.enrol()
        response = self.login(password="wrong-password")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("two_factor_required", response.data)
        self.assertEqual(response.data["detail"], "Invalid email or password.")

    def test_a_current_code_logs_in(self):
        secret, _codes, _token = self.enrol()
        response = self.login(code=self.code(secret))
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.data)

    def test_a_wrong_code_is_refused_without_a_session_and_logged_as_a_warning(self):
        secret, _codes, _token = self.enrol()
        Token.objects.all().delete()
        response = self.login(code=self.code(secret, steps=7))
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.data["detail"].startswith("Invalid verification code."))
        self.assertIn("date and time are set automatically", response.data["detail"])
        self.assertEqual(Token.objects.count(), 0)
        self.assertIn(("login_failed", "warning"), self.actions())

    def test_codes_from_one_period_either_side_are_accepted_for_clock_drift(self):
        secret, _codes, _token = self.enrol()
        for steps in (-1, 1):
            with self.subTest(steps=steps):
                self.user.refresh_from_db()
                User.objects.filter(pk=self.user.pk).update(totp_last_step=0)
                self.assertEqual(self.login(code=self.code(secret, steps)).status_code, 200)

    def test_codes_two_periods_away_are_refused(self):
        secret, _codes, _token = self.enrol()
        for steps in (-2, 2):
            with self.subTest(steps=steps):
                # Forget the replay guard so only the allowed clock drift is being tested.
                User.objects.filter(pk=self.user.pk).update(totp_last_step=0)
                self.assertEqual(self.login(code=self.code(secret, steps)).status_code, 401)

    def test_a_code_cannot_be_used_twice(self):
        secret, _codes, _token = self.enrol()
        code = self.code(secret)
        self.assertEqual(self.login(code=code).status_code, 200)
        self.assertEqual(self.login(code=code).status_code, 401)

    def test_after_a_code_is_used_the_next_one_works(self):
        secret, _codes, _token = self.enrol()
        self.assertEqual(self.login(code=self.code(secret)).status_code, 200)
        self.advance()
        self.assertEqual(self.login(code=self.code(secret)).status_code, 200)

    def test_an_earlier_code_is_refused_once_a_later_one_was_used(self):
        secret, _codes, _token = self.enrol()
        earlier = self.code(secret)
        self.assertEqual(self.login(code=self.code(secret, steps=1)).status_code, 200)
        self.assertEqual(self.login(code=earlier).status_code, 401)

    def test_spaces_in_the_code_are_ignored(self):
        secret, _codes, _token = self.enrol()
        code = self.code(secret)
        self.assertEqual(self.login(code=f"{code[:3]} {code[3:]} ").status_code, 200)

    def test_a_backup_code_logs_in_once_and_is_recorded(self):
        _secret, codes, _token = self.enrol()
        self.assertEqual(self.login(code=codes[0]).status_code, 200)
        self.assertIn(("backup_code_used", "warning"), self.actions())
        self.assertEqual(self.login(code=codes[0]).status_code, 401)
        self.assertEqual(self.login(code=codes[1]).status_code, 200)

    def test_backup_codes_ignore_case_and_hyphens(self):
        _secret, codes, _token = self.enrol()
        self.assertEqual(self.login(code=codes[0].replace("-", "").lower()).status_code, 200)

    def test_another_accounts_backup_code_does_not_work(self):
        _secret, codes, _token = self.enrol()
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        other.totp_enabled, other.totp_secret = True, self.user.totp_secret
        other.save()
        response = self.post("login/", {"email": "other@example.com", "password": PASSWORD, "code": codes[0]})
        self.assertEqual(response.status_code, 401)

    def test_guessing_codes_is_cut_off_per_account_even_from_many_addresses(self):
        secret, _codes, _token = self.enrol()
        for n in range(5):
            self.assertEqual(self.login(code="000000", ip=f"10.1.0.{n}").status_code, 401)
        response = self.login(code=self.code(secret), ip="10.1.0.99")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_wrong_passwords_and_wrong_codes_add_up_towards_the_same_lockout(self):
        secret, _codes, _token = self.enrol()
        for _ in range(3):
            self.login(password="wrong-password")
        for _ in range(2):
            self.login(code="000000")
        # Five failures from this address and account in total: even the right code is refused now.
        response = self.login(code=self.code(secret))
        self.assertEqual(response.status_code, 429)

    def test_text_that_is_not_ascii_is_refused_cleanly_not_with_a_crash(self):
        _secret, _codes, _token = self.enrol()
        for text in ("１２３４５６", "é23456", "١٢٣٤٥٦"):  # full-width, accented, Arabic-Indic digits
            with self.subTest(text=text):
                self.assertEqual(self.login(code=text).status_code, 401)

    def test_a_correct_code_clears_the_wrong_code_count(self):
        secret, _codes, _token = self.enrol()
        for n in range(4):
            self.login(code="000000", ip=f"10.2.0.{n}")
        self.assertEqual(self.login(code=self.code(secret), ip="10.2.0.50").status_code, 200)
        for n in range(4):
            self.assertEqual(self.login(code="000000", ip=f"10.3.0.{n}").status_code, 401)

    def test_logging_in_with_a_code_ends_the_earlier_session(self):
        secret, _codes, old_token = self.enrol()
        new_token = self.login(code=self.code(secret)).data["token"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {old_token}")
        self.assertEqual(client.get("/api/accounts/me/").status_code, 401)
        client.credentials(HTTP_AUTHORIZATION=f"Token {new_token}")
        self.assertEqual(client.get("/api/accounts/me/").status_code, 200)

    def test_resetting_the_password_does_not_get_around_two_factor(self):
        self.enrol()
        code = f"{urlsafe_base64_encode(force_bytes(self.user.pk))}:{default_token_generator.make_token(self.user)}"
        new_password = "Another-Correct-Horse-77"
        response = self.post(
            "password-reset/confirm/",
            {"code": code, "new_password1": new_password, "new_password2": new_password},
        )
        self.assertEqual(response.status_code, 200)
        after = self.login(password=new_password)
        self.assertEqual(after.status_code, 401)
        self.assertTrue(after.data["two_factor_required"])


class TurnOffAndRenewTests(TwoFactorTestCase):
    def setUp(self):
        super().setUp()
        self.secret, self.codes, _ = self.enrol()
        self.token = self.login(code=self.code(self.secret)).data["token"]
        self.advance()

    def disable(self, password=PASSWORD, code=None):
        code = self.code(self.secret) if code is None else code
        return self.post("two-factor/disable/", {"password": password, "code": code}, self.token)

    def test_turning_off_needs_the_right_password(self):
        self.assertEqual(self.disable(password="wrong").status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_turning_off_needs_a_right_code(self):
        self.assertEqual(self.disable(code="000000").status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_turning_off_removes_the_secret_and_the_backup_codes(self):
        self.assertEqual(self.disable().status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual((self.user.totp_secret, self.user.totp_enabled), ("", False))
        self.assertEqual(BackupCode.objects.count(), 0)
        self.assertEqual(self.login().status_code, 200)
        self.assertIn(("two_factor_disabled", "warning"), self.actions())

    def test_a_backup_code_is_enough_to_turn_it_off(self):
        self.assertEqual(self.disable(code=self.codes[0]).status_code, 200)

    def test_turning_off_when_it_is_off_is_refused(self):
        self.disable()
        self.advance()
        self.assertEqual(self.disable().status_code, 400)

    def test_wrong_attempts_to_turn_it_off_are_rate_limited(self):
        for _ in range(5):
            self.disable(code="000000")
        self.assertEqual(self.disable().status_code, 429)

    def test_renewing_gives_new_codes_and_the_old_ones_stop_working(self):
        response = self.post(
            "two-factor/backup-codes/", {"password": PASSWORD, "code": self.code(self.secret)}, self.token
        )
        self.assertEqual(response.status_code, 200)
        new = response.data["backup_codes"]
        self.assertEqual(len(new), 8)
        self.assertFalse(set(new) & set(self.codes))
        self.assertEqual(BackupCode.objects.count(), 8)
        self.assertEqual(self.login(code=self.codes[0]).status_code, 401)
        self.assertEqual(self.login(code=new[0]).status_code, 200)
        self.assertIn(("backup_codes_renewed", "info"), self.actions())

    def test_renewing_needs_the_password_and_a_code(self):
        self.assertEqual(
            self.post("two-factor/backup-codes/", {"password": "wrong", "code": self.code(self.secret)}, self.token).status_code,
            400,
        )
        self.assertEqual(
            self.post("two-factor/backup-codes/", {"password": PASSWORD, "code": "000000"}, self.token).status_code,
            400,
        )


class TwoFactorUnitTests(TwoFactorTestCase):
    def test_a_code_that_is_not_six_digits_is_rejected_without_a_secret_lookup(self):
        for text in ("", "12345", "1234567", "abcdef", "12 34"):
            with self.subTest(text=text):
                self.assertFalse(twofactor.verify_totp(self.user, text))

    def test_two_requests_racing_with_the_same_code_cannot_both_succeed(self):
        secret, _ = twofactor.begin_enrolment(self.user)
        code = self.code(secret)
        first = User.objects.get(pk=self.user.pk)
        second = User.objects.get(pk=self.user.pk)  # loaded before the first request finished
        self.assertTrue(twofactor.verify_totp(first, code))
        self.assertFalse(twofactor.verify_totp(second, code))

    def test_two_requests_racing_with_the_same_backup_code_cannot_both_succeed(self):
        codes = twofactor.generate_backup_codes(self.user)
        self.assertTrue(twofactor.use_backup_code(self.user, codes[0]))
        self.assertFalse(twofactor.use_backup_code(self.user, codes[0]))

    def test_the_same_backup_code_hashes_differently_for_different_users(self):
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        self.assertNotEqual(
            twofactor._hash_backup_code(self.user, "ABCDEFGHJKLMNPQR"),
            twofactor._hash_backup_code(other, "ABCDEFGHJKLMNPQR"),
        )

    def test_a_backup_code_of_the_wrong_length_is_rejected(self):
        codes = twofactor.generate_backup_codes(self.user)
        self.assertFalse(twofactor.use_backup_code(self.user, codes[0][:-1]))
        self.assertFalse(twofactor.use_backup_code(self.user, codes[0] + "A"))

    def test_generating_codes_replaces_the_old_ones(self):
        old = twofactor.generate_backup_codes(self.user)
        new = twofactor.generate_backup_codes(self.user)
        self.assertEqual(twofactor.backup_codes_left(self.user), 8)
        self.assertFalse(twofactor.use_backup_code(self.user, old[0]))
        self.assertTrue(twofactor.use_backup_code(self.user, new[0]))
        self.assertEqual(twofactor.backup_codes_left(self.user), 7)
