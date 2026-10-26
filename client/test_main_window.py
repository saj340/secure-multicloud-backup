import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from unittest import mock

from PySide6.QtWidgets import QMessageBox

from .test_gui import GuiTestCase, wait_until
from .windows.main_window import MainWindow
from .workers import long_task_running, run_in_background

YES = QMessageBox.StandardButton.Yes
NO = QMessageBox.StandardButton.No
ASK = "client.windows.main_window.QMessageBox.question"


class InterruptingATaskTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        # Jobs left by earlier tests are only cleared once their events are processed.
        self.assertTrue(wait_until(lambda: not long_task_running()))
        self.logged_out = []
        self.window = MainWindow(self.api, on_logout=lambda: self.logged_out.append(True))
        self.window.set_user(self.api.me())
        self.window.show()
        self.addCleanup(self.window.deleteLater)

        self.release = threading.Event()
        self.addCleanup(self.release.set)  # runs first, so no worker outlives the test

    def start_long_task(self):
        run_in_background(lambda report: self.release.wait(10), on_progress=lambda *args: None)
        self.assertTrue(long_task_running())

    def test_closing_during_a_backup_asks_first_and_can_be_cancelled(self):
        self.start_long_task()
        with mock.patch(ASK, return_value=NO) as ask:
            self.window.close()

        self.assertTrue(self.window.isVisible())
        self.assertEqual(ask.call_count, 1)
        self.assertIn("still running", ask.call_args.args[2])

    def test_closing_during_a_backup_can_be_confirmed(self):
        self.start_long_task()
        with mock.patch(ASK, return_value=YES):
            self.window.close()
        self.assertFalse(self.window.isVisible())

    def test_closing_with_nothing_running_does_not_ask(self):
        with mock.patch(ASK) as ask:
            self.window.close()
        ask.assert_not_called()
        self.assertFalse(self.window.isVisible())

    def test_logging_out_during_a_backup_asks_first(self):
        self.start_long_task()

        with mock.patch(ASK, return_value=NO):
            self.window.logout_button.click()
        self.assertEqual(self.logged_out, [])
        self.assertIsNotNone(self.api.token)

        with mock.patch(ASK, return_value=YES):
            self.window.logout_button.click()
        self.assertTrue(wait_until(lambda: self.logged_out))
        self.assertIsNone(self.api.token)


class NavigationTests(GuiTestCase):
    def test_every_screen_is_listed_in_order_and_opens_without_error(self):
        window = MainWindow(self.api)
        self.addCleanup(window.deleteLater)
        window.set_user(self.api.me())

        names = [window.sidebar.item(row).text() for row in range(window.sidebar.count())]
        self.assertEqual(
            names,
            [
                "Dashboard",
                "New Backup",
                "Backup Sets",
                "Backup History",
                "Recover Files",
                "Backup Schedules",
                "Cloud Providers",
                "Activity",
                "Settings",
            ],
        )

        for row in range(len(names)):
            window.sidebar.setCurrentRow(row)
            self.assertEqual(window.pages.currentIndex(), row)
        self.assertTrue(
            wait_until(
                lambda: not (window.history._busy
                    or window.recover._busy
                    or window.providers._busy
                    or window.activity._busy
                    or window.sets._busy
                )
            )
        )
        for page in (window.history, window.recover, window.providers, window.activity, window.sets):
            self.assertEqual(page.status_label.text(), "")
