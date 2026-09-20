import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil

from django.test import SimpleTestCase

from .api_client import ApiClient, ApiError
from .test_client import EMAIL, api_url
from .test_gui import GuiTestCase, wait_until
from .windows.activity_page import ActivityPage, describe_event, describe_level


class DescribeTests(SimpleTestCase):
    def test_known_events_get_readable_names(self):
        self.assertEqual(describe_event("login_failed"), "Failed sign-in")
        self.assertEqual(describe_event("provider_unreachable"), "Provider unreachable")

    def test_an_unknown_event_is_still_readable(self):
        self.assertEqual(describe_event("something_new_happened"), "Something new happened")

    def test_levels_have_a_label_and_colour(self):
        self.assertEqual(describe_level("warning")[0], "Warning")
        self.assertEqual(describe_level("odd")[0], "Odd")


class ActivityPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = ActivityPage(self.api)
        self.addCleanup(self.page.deleteLater)

    def load(self):
        self.page.refresh()
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def rows(self):
        return [
            [self.page.table.item(row, column).text() for column in range(4)]
            for row in range(self.page.table.rowCount())
        ]

    def test_it_shows_what_this_account_has_done_newest_first(self):
        self.connect_via_api("alpha")
        self.load()
        events = [row[2] for row in self.rows()]
        # register happened before login (the test base class does both), then the provider.
        self.assertEqual(events[:3], ["Provider connected", "Signed in", "Account created"])
        self.assertEqual(self.page.status_label.text(), "")

    def test_an_outage_shows_up_as_a_warning_and_can_be_filtered_to(self):
        folder = self.connect_via_api("alpha")
        self.connect_via_api("beta")
        shutil.rmtree(folder)
        provider = next(p for p in self.api.list_providers() if p["display_name"] == "alpha")
        self.api.check_provider(provider["id"])

        self.load()
        self.assertGreater(len(self.rows()), 1)

        self.page.problems_only.setChecked(True)
        problems = self.rows()
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][1:3], ["Warning", "Provider unreachable"])
        self.assertIn("alpha", problems[0][3])

    def test_the_filter_says_so_when_there_are_no_problems(self):
        self.load()
        self.page.problems_only.setChecked(True)
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertFalse(self.page.empty_label.isHidden())
        self.assertEqual(self.page.empty_label.text(), "No warnings or errors.")

    def test_the_screen_never_shows_the_ip_address_of_a_failed_login(self):
        with self.assertRaises(ApiError):
            ApiClient(api_url(self)).login(EMAIL, "wrong-password-123")
        self.load()
        self.assertNotIn("127.0.0.1", str(self.rows()))
