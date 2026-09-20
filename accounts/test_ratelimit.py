from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import ratelimit
from .models import RateLimitCounter, User

PASSWORD = "CorrectHorseBatteryStaple9"
NEW_PASSWORD = "Another-Correct-Horse-77"


def limits(**changes):
    return override_settings(RATE_LIMITS={**settings.RATE_LIMITS, **changes})


class RateLimitModuleTests(TestCase):
    SCOPE = "login_account"  # 5 attempts per 15 minutes

    def test_nothing_is_blocked_at_first(self):
        self.assertEqual(ratelimit.retry_after(self.SCOPE, "someone"), 0)

    def test_blocked_exactly_when_the_limit_is_reached(self):
        for _ in range(4):
            ratelimit.record(self.SCOPE, "someone")
        self.assertEqual(ratelimit.retry_after(self.SCOPE, "someone"), 0)

        ratelimit.record(self.SCOPE, "someone")
        wait = ratelimit.retry_after(self.SCOPE, "someone")
        self.assertTrue(0 < wait <= 15 * 60)

    def test_the_window_passing_lifts_the_block_and_counting_restarts(self):
        for _ in range(5):
            ratelimit.record(self.SCOPE, "someone")
        RateLimitCounter.objects.update(window_started=timezone.now() - timedelta(minutes=16))
        self.assertEqual(ratelimit.retry_after(self.SCOPE, "someone"), 0)

        ratelimit.record(self.SCOPE, "someone")
        self.assertEqual(RateLimitCounter.objects.get().count, 1)

    def test_reset_clears_the_count(self):
        for _ in range(5):
            ratelimit.record(self.SCOPE, "someone")
        ratelimit.reset(self.SCOPE, "someone")
        self.assertEqual(ratelimit.retry_after(self.SCOPE, "someone"), 0)

    def test_identifiers_and_scopes_are_independent(self):
        for _ in range(5):
            ratelimit.record(self.SCOPE, "someone")
        self.assertEqual(ratelimit.retry_after(self.SCOPE, "someone else"), 0)
        self.assertEqual(ratelimit.retry_after("login_ip", "someone"), 0)

    def test_identifiers_are_stored_hashed_not_in_the_clear(self):
        ratelimit.record(self.SCOPE, "1.2.3.4|secret@example.com")
        stored = RateLimitCounter.objects.get()
        self.assertNotIn("secret", stored.key)
        self.assertNotIn("1.2.3.4", stored.key)

    @limits(login_account=(1, timedelta(minutes=10)), login_ip=(1, timedelta(minutes=30)))
    def test_several_checks_report_the_longest_wait(self):
        ratelimit.record("login_account", "a")
        ratelimit.record("login_ip", "b")
        wait = ratelimit.blocked_for(("login_account", "a"), ("login_ip", "b"))
        self.assertTrue(10 * 60 < wait <= 30 * 60)
        self.assertEqual(ratelimit.blocked_for(("login_account", "unrelated")), 0)
        self.assertEqual(ratelimit.blocked_for(), 0)

    def test_long_finished_windows_are_tidied_up(self):
        RateLimitCounter.objects.create(
            scope="login_ip", key="old", count=3, window_started=timezone.now() - timedelta(days=3)
        )
        ratelimit.record(self.SCOPE, "someone")
        self.assertFalse(RateLimitCounter.objects.filter(key="old").exists())


class ThrottledEndpointTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="victim@example.com", password=PASSWORD)

    def post(self, path, data, ip="10.0.0.1", token=None):
        client = APIClient(REMOTE_ADDR=ip)
        if token:
            client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        return client.post(path, data, format="json")

    def login(self, password, email="victim@example.com", ip="10.0.0.1"):
        return self.post("/api/accounts/login/", {"email": email, "password": password}, ip=ip)


class LoginThrottleTests(ThrottledEndpointTestCase):
    def test_five_wrong_passwords_lock_out_even_the_right_one(self):
        for _ in range(5):
            self.assertEqual(self.login("wrong-password-1").status_code, 401)

        response = self.login(PASSWORD)  # correct, but the guesser can't learn that
        self.assertEqual(response.status_code, 429)
        self.assertIn("Too many failed login attempts", response.data["detail"])
        self.assertTrue(0 < int(response["Retry-After"]) <= 15 * 60)

    def test_the_lockout_is_per_ip_so_the_real_owner_elsewhere_can_still_log_in(self):
        for _ in range(5):
            self.login("wrong-password-1", ip="6.6.6.6")
        self.assertEqual(self.login(PASSWORD, ip="6.6.6.6").status_code, 429)
        self.assertEqual(self.login(PASSWORD, ip="10.0.0.9").status_code, 200)

    def test_a_successful_login_clears_that_ips_count_for_the_account(self):
        for _ in range(4):
            self.login("wrong-password-1")
        self.assertEqual(self.login(PASSWORD).status_code, 200)
        for _ in range(4):
            self.assertEqual(self.login("wrong-password-1").status_code, 401)  # not 429

    def test_an_unknown_email_is_throttled_and_answered_exactly_like_a_real_one(self):
        unknown = self.login("whatever-1", email="nobody@example.com", ip="1.1.1.1")
        known = self.login("whatever-1", ip="2.2.2.2")
        self.assertEqual((unknown.status_code, unknown.data), (known.status_code, known.data))

        # Each gets the same treatment from its own IP, so the counters start equal.
        for email, ip in (("nobody@example.com", "3.3.3.3"), ("victim@example.com", "4.4.4.4")):
            with self.subTest(email=email):
                codes = [self.login("whatever-1", email=email, ip=ip).status_code for _ in range(6)]
                self.assertEqual(codes, [401] * 5 + [429])  # blocked at the same point either way

    @limits(login_ip=(3, timedelta(minutes=15)))
    def test_one_ip_guessing_across_many_accounts_is_stopped(self):
        for index in range(3):
            self.login("guess-1", email=f"user{index}@example.com", ip="7.7.7.7")
        self.assertEqual(self.login("guess-1", email="fresh@example.com", ip="7.7.7.7").status_code, 429)
        self.assertEqual(self.login("guess-1", email="fresh@example.com", ip="8.8.8.8").status_code, 401)

    def test_malformed_requests_are_not_counted(self):
        for _ in range(10):
            self.assertEqual(self.post("/api/accounts/login/", {"email": "not-an-email"}).status_code, 400)
        self.assertEqual(self.login(PASSWORD).status_code, 200)

    def test_the_block_ends_when_the_window_does(self):
        for _ in range(5):
            self.login("wrong-password-1")
        self.assertEqual(self.login(PASSWORD).status_code, 429)

        RateLimitCounter.objects.update(window_started=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.login(PASSWORD).status_code, 200)


class PasswordResetThrottleTests(ThrottledEndpointTestCase):
    REQUEST = "/api/accounts/password-reset/request/"
    CONFIRM = "/api/accounts/password-reset/confirm/"

    def test_reset_codes_for_one_email_are_capped_to_protect_the_inbox(self):
        for _ in range(3):
            self.assertEqual(self.post(self.REQUEST, {"email": "victim@example.com"}).status_code, 200)
        blocked = self.post(self.REQUEST, {"email": "victim@example.com"})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(len(mail.outbox), 3)

    def test_an_unknown_email_hits_the_same_limit_at_the_same_point(self):
        for _ in range(3):
            self.post(self.REQUEST, {"email": "nobody@example.com"})
        self.assertEqual(self.post(self.REQUEST, {"email": "nobody@example.com"}).status_code, 429)
        self.assertEqual(mail.outbox, [])

    def test_a_different_email_is_not_affected_by_the_cap_on_another(self):
        for _ in range(4):
            self.post(self.REQUEST, {"email": "victim@example.com"})
        self.assertEqual(self.post(self.REQUEST, {"email": "other@example.com"}).status_code, 200)

    @limits(reset_request_ip=(2, timedelta(hours=1)))
    def test_one_ip_cannot_request_codes_for_many_emails(self):
        self.post(self.REQUEST, {"email": "a@example.com"})
        self.post(self.REQUEST, {"email": "b@example.com"})
        self.assertEqual(self.post(self.REQUEST, {"email": "c@example.com"}).status_code, 429)

    @limits(reset_confirm_ip=(3, timedelta(minutes=15)))
    def test_guessing_reset_codes_is_stopped(self):
        bad = {"code": "bogus:code", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
        for _ in range(3):
            self.assertEqual(self.post(self.CONFIRM, bad).status_code, 400)
        self.assertEqual(self.post(self.CONFIRM, bad).status_code, 429)
        self.assertEqual(self.post(self.CONFIRM, bad, ip="10.9.9.9").status_code, 400)


class ChangePasswordThrottleTests(ThrottledEndpointTestCase):
    URL = "/api/accounts/change-password/"

    def setUp(self):
        super().setUp()
        self.token = self.login(PASSWORD).data["token"]

    def attempt(self, current):
        return self.post(
            self.URL,
            {"current_password": current, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
            token=self.token,
        )

    def test_someone_with_a_stolen_token_cannot_guess_the_current_password(self):
        for _ in range(5):
            self.assertEqual(self.attempt("wrong-guess-1").status_code, 400)

        response = self.attempt(PASSWORD)  # even the right one, so a guess can't be confirmed
        self.assertEqual(response.status_code, 429)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_successful_change_clears_the_count(self):
        for _ in range(4):
            self.attempt("wrong-guess-1")
        self.assertEqual(self.attempt(PASSWORD).status_code, 200)
        self.assertEqual(RateLimitCounter.objects.filter(scope="change_password_user").count(), 0)


class RegisterThrottleTests(ThrottledEndpointTestCase):
    @limits(register_ip=(3, timedelta(hours=1)))
    def test_one_ip_cannot_mass_create_accounts(self):
        def register(index, ip="10.0.0.1"):
            return self.post(
                "/api/accounts/register/",
                {
                    "email": f"new{index}@example.com",
                    "full_name": "",
                    "password1": PASSWORD,
                    "password2": PASSWORD,
                },
                ip=ip,
            )

        for index in range(3):
            self.assertEqual(register(index).status_code, 201)
        self.assertEqual(register(3).status_code, 429)
        self.assertEqual(register(4, ip="10.0.0.2").status_code, 201)
