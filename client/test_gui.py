import os

# Must be set before any Qt application is created: renders without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import threading
import time
from pathlib import Path

from django.test import LiveServerTestCase, SimpleTestCase, override_settings
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .api_client import ApiClient, ApiError
from .backup_service import load_backups, restore_version, run_backup
from .main import AppWindows
from .test_client import EMAIL, PASSWORD, api_url
from .windows.dashboard_page import DashboardPage
from .windows.login_window import LoginWindow
from .windows.providers_page import ProvidersPage
from .workers import run_in_background

app = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


class BackgroundTests(SimpleTestCase):
    def test_work_runs_off_the_ui_thread_and_callbacks_run_on_it(self):
        seen = {}

        def work():
            seen["worker"] = threading.get_ident()
            return 42

        run_in_background(
            work, on_success=lambda r: seen.update(result=r, callback=threading.get_ident())
        )
        self.assertTrue(wait_until(lambda: "result" in seen))
        self.assertEqual(seen["result"], 42)
        self.assertNotEqual(seen["worker"], threading.get_ident())
        self.assertEqual(seen["callback"], threading.get_ident())

    def test_failures_are_reported_as_readable_messages_not_swallowed(self):
        messages = []

        def api_failure():
            raise ApiError("Server said no", 400)

        def other_failure():
            raise ValueError("bad value")

        run_in_background(api_failure, on_error=messages.append)
        run_in_background(other_failure, on_error=messages.append)
        self.assertTrue(wait_until(lambda: len(messages) == 2))
        self.assertEqual(sorted(messages), ["Server said no", "bad value"])

    def test_progress_updates_arrive_in_order_on_the_ui_thread(self):
        updates = []

        def work(report):
            for step in range(3):
                report("step", step)
            return "done"

        run_in_background(
            work,
            on_success=lambda result: updates.append(("result", result)),
            on_progress=lambda stage, step: updates.append((stage, step, threading.get_ident())),
        )
        self.assertTrue(wait_until(lambda: any(u[0] == "result" for u in updates)))
        self.assertEqual(
            [u[:2] for u in updates],
            [("step", 0), ("step", 1), ("step", 2), ("result", "done")],
        )
        self.assertEqual({u[2] for u in updates if u[0] == "step"}, {threading.get_ident()})


@override_settings(ALLOW_LOCAL_FOLDER_PROVIDERS=True)
class GuiTestCase(LiveServerTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

        self.api = ApiClient(api_url(self))
        self.api.register(EMAIL, "Gui Test", PASSWORD, PASSWORD)
        self.api.login(EMAIL, PASSWORD)
        self.addCleanup(self.drain_background_jobs)

    def drain_background_jobs(self):
        # A request still in flight when this test's data is wiped would come back
        # as a 401, and trip the "session expired" handler of whichever test runs next.
        from . import workers

        wait_until(lambda: not workers._active, timeout=10)

    def make_folder(self, name):
        folder = self.tmp / name
        folder.mkdir()
        return folder

    def connect_via_api(self, name):
        folder = self.make_folder(name)
        self.api.connect_provider("local_folder", name, {"path": str(folder)})
        return folder


class ProvidersPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = ProvidersPage(self.api)
        self.addCleanup(self.page.deleteLater)

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def cell(self, row, column):
        return self.page.table.item(row, column).text()

    def add(self, name):
        folder = self.make_folder(name)
        self.page.connect_provider("local_folder", name, {"path": str(folder)})
        self.settle()
        return folder

    def test_with_no_providers_it_warns_that_two_are_needed(self):
        self.page.refresh()
        self.settle()
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertFalse(self.page.warning_label.isHidden())
        self.assertIn("at least 2", self.page.warning_label.text())

    def test_connecting_providers_lists_them_and_clears_the_warning(self):
        for name in ("alpha", "beta", "gamma"):
            self.add(name)

        self.assertEqual(self.page.table.rowCount(), 3)
        self.assertEqual(
            sorted(self.cell(row, 0) for row in range(3)), ["alpha", "beta", "gamma"]
        )
        self.assertEqual({self.cell(row, 2) for row in range(3)}, {"Connected"})
        self.assertTrue(self.page.warning_label.isHidden())

    def test_a_failed_connection_shows_the_servers_message(self):
        self.page.connect_provider("local_folder", "broken", {"path": str(self.tmp / "missing")})
        self.settle()
        self.assertIn("Could not reach", self.page.status_label.text())
        self.assertEqual(self.page.table.rowCount(), 0)

    def test_checking_a_provider_notices_an_outage(self):
        folder = self.add("alpha")
        shutil.rmtree(folder)

        provider_id = self.page.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        self.page.check_provider(provider_id)
        self.settle()
        self.assertEqual(self.cell(0, 2), "Error")

    def test_disconnecting_marks_the_provider_disconnected_and_disables_its_buttons(self):
        self.add("alpha")
        provider_id = self.page.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        self.page.disconnect_provider(provider_id)
        self.settle()

        self.assertEqual(self.cell(0, 2), "Disconnected")
        self.page.table.selectRow(0)
        self.assertFalse(self.page.check_button.isEnabled())
        self.assertFalse(self.page.disconnect_button.isEnabled())

    def test_action_buttons_follow_the_selection(self):
        self.add("alpha")
        self.assertFalse(self.page.check_button.isEnabled())
        self.assertFalse(self.page.disconnect_button.isEnabled())

        self.page.table.selectRow(0)
        self.assertTrue(self.page.check_button.isEnabled())
        self.assertTrue(self.page.disconnect_button.isEnabled())


class DashboardPageTests(GuiTestCase):
    def test_shows_counts_and_readiness(self):
        page = DashboardPage(self.api)
        self.addCleanup(page.deleteLater)

        self.connect_via_api("alpha")
        page.refresh()
        self.assertTrue(wait_until(lambda: page.providers_value.text() != "–"))
        self.assertEqual(page.providers_value.text(), "1")
        # While setup is unfinished the getting-started checklist does the guiding
        # (see test_first_run.py), so the readiness line stays empty.
        self.assertFalse(page.setup_frame.isHidden())
        self.assertEqual(page.status_label.text(), "")

        self.connect_via_api("beta")
        document = self.tmp / "doc.txt"
        document.write_text("hello")
        run_backup(self.api, "docs", [document])

        page.refresh()
        self.assertTrue(wait_until(lambda: page.providers_value.text() == "2"))
        self.assertEqual(page.jobs_value.text(), "1")
        self.assertEqual(page.recoveries_value.text(), "0")

        # Once a backup and a restore have both worked, the checklist gives way to this.
        version = load_backups(self.api)[0]["files"][0]["versions"][0]
        restore_version(self.api, version["id"], self.tmp / "restored.txt")
        page.refresh()
        self.assertTrue(wait_until(lambda: page.recoveries_value.text() == "1"))
        self.assertTrue(page.setup_frame.isHidden())
        self.assertIn("Ready", page.status_label.text())


class LoginAndNavigationTests(GuiTestCase):
    def test_a_successful_login_reports_the_user_and_clears_the_password(self):
        users = []
        window = LoginWindow(ApiClient(api_url(self)), on_login_success=users.append)
        self.addCleanup(window.deleteLater)

        window.email_input.setText(EMAIL)
        window.password_input.setText(PASSWORD)
        window.login_button.click()

        self.assertEqual([u["email"] for u in users], [EMAIL])
        self.assertEqual(window.password_input.text(), "")

    def test_a_bad_login_shows_the_error_and_reports_nothing(self):
        users = []
        window = LoginWindow(ApiClient(api_url(self)), on_login_success=users.append)
        self.addCleanup(window.deleteLater)

        window.email_input.setText(EMAIL)
        window.password_input.setText("wrong-password")
        window.login_button.click()

        self.assertEqual(users, [])
        self.assertEqual(window.status_label.text(), "Invalid email or password.")

    def test_login_opens_the_main_window_and_logout_returns_to_login(self):
        api = ApiClient(api_url(self))
        windows = AppWindows(api)
        self.addCleanup(lambda: [w.close() for w in windows._windows])
        windows.start()
        self.assertTrue(windows.login_window.isVisible())

        windows.login_window.email_input.setText(EMAIL)
        windows.login_window.password_input.setText(PASSWORD)
        windows.login_window.login_button.click()

        self.assertTrue(windows.main_window.isVisible())
        self.assertFalse(windows.login_window.isVisible())
        self.assertEqual(windows.main_window.user_label.text(), EMAIL)
        self.assertTrue(wait_until(lambda: windows.main_window.dashboard.providers_value.text() != "–"))

        windows.main_window.logout_button.click()
        self.assertTrue(wait_until(lambda: windows.login_window.isVisible()))
        self.assertFalse(windows.main_window.isVisible())
        self.assertIsNone(api.token)
