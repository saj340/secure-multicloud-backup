import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
from unittest import mock

from PySide6.QtCore import Qt

from . import workers
from .api_client import ApiClient, ApiError
from .health_runner import MIN_GAP_SECONDS, HealthRunner, check_all
from .main import AppWindows
from .test_client import api_url
from .test_gui import GuiTestCase, wait_until
from .windows.main_window import MainWindow


def provider_id(api, name):
    return next(p["id"] for p in api.list_providers() if p["display_name"] == name)


class CheckAllTests(GuiTestCase):
    def test_it_rechecks_every_provider_and_notices_the_one_that_went_away(self):
        gone = self.connect_via_api("alpha")
        self.connect_via_api("beta")
        shutil.rmtree(gone)

        statuses = {p["display_name"]: p["status"] for p in check_all(self.api)}

        self.assertEqual(statuses, {"alpha": "error", "beta": "connected"})

    def test_a_provider_that_was_disconnected_on_purpose_is_left_alone(self):
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        self.api.disconnect_provider(provider_id(self.api, "alpha"))

        with mock.patch.object(self.api, "check_provider", wraps=self.api.check_provider) as spy:
            statuses = {p["display_name"]: p["status"] for p in check_all(self.api)}

        self.assertEqual(statuses, {"alpha": "disconnected", "beta": "connected"})
        self.assertEqual([call.args[0] for call in spy.call_args_list], [provider_id(self.api, "beta")])

    def test_one_check_failing_does_not_hide_the_others(self):
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        slow = provider_id(self.api, "alpha")
        real = self.api.check_provider

        def flaky(pid):
            if pid == slow:
                raise ApiError("timed out")
            return real(pid)

        with mock.patch.object(self.api, "check_provider", side_effect=flaky):
            statuses = {p["display_name"]: p["status"] for p in check_all(self.api)}

        # The one that couldn't be checked keeps the status it already had.
        self.assertEqual(statuses, {"alpha": "connected", "beta": "connected"})


class HealthRunnerTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.folders = {name: self.connect_via_api(name) for name in ("alpha", "beta")}
        self.results = []
        self.runner = HealthRunner(self.api, on_result=lambda *args: self.results.append(args))
        self.runner.start(first_check_ms=None)
        self.addCleanup(self.runner.deleteLater)
        self.addCleanup(self.runner.stop)

    def round(self, force=True):
        before = len(self.results)
        self.runner.check_now(force=force)
        self.assertTrue(wait_until(lambda: len(self.results) > before))
        return self.results[-1]

    def quiet_for_a_moment(self):
        wait_until(lambda: False, timeout=0.5)

    def test_the_first_round_is_a_baseline_not_a_change(self):
        self.assertEqual(self.round(), ([], False))

    def test_a_provider_going_down_and_coming_back_is_reported_both_times(self):
        self.round()

        shutil.rmtree(self.folders["alpha"])
        self.assertEqual(self.round(), (["alpha"], True))

        self.folders["alpha"].mkdir()
        self.assertEqual(self.round(), ([], True))

    def test_a_round_where_nothing_moved_is_not_a_change(self):
        shutil.rmtree(self.folders["alpha"])
        self.round()
        self.assertEqual(self.round(), (["alpha"], False))

    def test_opening_screens_in_quick_succession_checks_only_once(self):
        self.round()
        self.runner.check_now()  # not forced: it was only a moment ago
        self.quiet_for_a_moment()
        self.assertEqual(len(self.results), 1)
        self.assertGreater(MIN_GAP_SECONDS, 0)

    def test_it_does_nothing_until_started_and_after_it_is_stopped(self):
        self.runner.stop()
        self.runner.check_now(force=True)
        self.quiet_for_a_moment()
        self.assertEqual(self.results, [])

    def test_it_does_nothing_when_nobody_is_logged_in(self):
        token, self.api.token = self.api.token, None
        self.addCleanup(setattr, self.api, "token", token)
        self.runner.check_now(force=True)
        self.quiet_for_a_moment()
        self.assertEqual(self.results, [])

    def test_a_round_that_finishes_after_logout_is_thrown_away(self):
        self.runner.check_now(force=True)
        self.runner.stop()  # logged out while the round was in flight
        self.assertTrue(wait_until(lambda: not workers._active))
        self.assertEqual(self.results, [])

    def test_an_unreachable_server_is_quiet_and_does_not_wedge_the_runner(self):
        dead = ApiClient("http://127.0.0.1:9/api")  # nothing listens on port 9
        dead.token = "irrelevant"
        runner = HealthRunner(dead, on_result=lambda *args: self.results.append(args))
        runner.start(first_check_ms=None)
        self.addCleanup(runner.deleteLater)
        self.addCleanup(runner.stop)

        runner.check_now(force=True)
        self.assertTrue(wait_until(lambda: not runner._running))
        self.assertEqual(self.results, [])
        runner.check_now(force=True)  # can try again later
        self.assertTrue(wait_until(lambda: not runner._running))


class MainWindowHealthTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.api)
        self.addCleanup(self.window.deleteLater)
        self.window.set_user(self.api.me())
        self.window.health.start(first_check_ms=None)
        self.addCleanup(self.window.health.stop)
        self.folders = {name: self.connect_via_api(name) for name in ("alpha", "beta")}

    def open_screen(self, name):
        row = self.window.sidebar.row(
            self.window.sidebar.findItems(name, Qt.MatchFlag.MatchExactly)[0]
        )
        self.window.sidebar.setCurrentRow(row)

    def status_in_table(self, name):
        table = self.window.providers.table
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == name:
                return table.item(row, 2).text()

    def test_opening_the_providers_screen_notices_an_outage_and_warns_in_the_header(self):
        shutil.rmtree(self.folders["alpha"])

        self.open_screen("Cloud Providers")

        self.assertTrue(wait_until(lambda: self.window.health_status.text() != ""))
        self.assertEqual(self.window.health_status.text(), "⚠ alpha is unreachable")

    def test_a_change_redraws_the_screen_that_is_showing_provider_health(self):
        self.open_screen("Cloud Providers")
        self.assertTrue(wait_until(lambda: self.status_in_table("alpha") == "Connected"))
        self.assertTrue(wait_until(lambda: not self.window.providers._busy))
        self.window.health.check_now(force=True)  # baseline round
        self.assertTrue(wait_until(lambda: self.window.health._last_statuses is not None))

        shutil.rmtree(self.folders["alpha"])
        self.window.health.check_now(force=True)

        self.assertTrue(wait_until(lambda: self.status_in_table("alpha") == "Error"))

    def test_several_providers_down_are_counted_in_the_header(self):
        for folder in self.folders.values():
            shutil.rmtree(folder)
        self.open_screen("Dashboard")  # already there; force a round instead
        self.window.health.check_now(force=True)
        self.assertTrue(wait_until(lambda: "providers are unreachable" in self.window.health_status.text()))
        self.assertEqual(self.window.health_status.text(), "⚠ 2 providers are unreachable")

    def test_logging_out_stops_checking_and_clears_the_warning(self):
        windows = AppWindows(self.api)
        self.addCleanup(windows.main_window.deleteLater)
        windows.main_window.health.start(first_check_ms=None)
        windows.main_window.health_status.setText("⚠ alpha is unreachable")

        windows.show_login()

        self.assertEqual(windows.main_window.health_status.text(), "")
        self.assertFalse(windows.main_window.health._timer.isActive())
