import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import threading
from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone
from PySide6.QtWidgets import QDialog

from backups.models import BackupSchedule

from .api_client import ApiClient
from .main import AppWindows
from .schedule_runner import ScheduleRunner
from .scheduler import due_schedules, run_schedule
from .test_client import EMAIL, PASSWORD, api_url
from .test_gui import GuiTestCase, wait_until
from .windows.schedule_dialog import ScheduleDialog, local_utc_offset_minutes
from .windows.schedules_page import SchedulesPage
from .workers import run_in_background, set_unauthorized_handler


class ScheduleTestCase(GuiTestCase):
    def setUp(self):
        super().setUp()
        for index in range(2):
            self.connect_via_api(f"provider{index}")
        self.source = self.tmp / "docs"
        self.source.mkdir()
        (self.source / "a.txt").write_text("alpha " * 100)
        (self.source / "b.txt").write_text("bravo " * 100)

    def make_schedule(self, name="Docs", **overrides):
        fields = {
            "name": name,
            "source_path": str(self.source),
            "frequency": "daily",
            "run_at": "02:00",
            **overrides,
        }
        return self.api.create_schedule(**fields)

    def make_due(self, schedule):
        BackupSchedule.objects.filter(pk=schedule["id"]).update(
            created_at=timezone.now() - timedelta(days=3)
        )

    def schedule_state(self, schedule):
        return next(s for s in self.api.list_schedules() if s["id"] == schedule["id"])


class SchedulerFunctionTests(ScheduleTestCase):
    def test_a_due_schedule_is_backed_up_as_a_dated_snapshot_and_recorded(self):
        schedule = self.make_schedule()
        self.make_due(schedule)
        self.assertEqual([s["id"] for s in due_schedules(self.api)], [schedule["id"]])

        result = run_schedule(self.api, schedule)

        self.assertEqual(result.status, "completed")
        jobs = self.api.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertRegex(jobs[0]["name"], r"^Docs \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(jobs[0]["file_count"], 2)

        state = self.schedule_state(schedule)
        self.assertEqual(state["last_result"], "completed")
        self.assertFalse(state["is_due"])
        self.assertEqual(due_schedules(self.api), [])

    def test_each_run_is_its_own_snapshot(self):
        schedule = self.make_schedule()
        run_schedule(self.api, schedule)
        (self.source / "new.txt").write_text("appeared later")
        run_schedule(self.api, schedule)

        counts = sorted(job["file_count"] for job in self.api.list_jobs())
        self.assertEqual(counts, [2, 3])

    def test_only_active_schedules_that_are_due_are_returned(self):
        due = self.make_schedule("Due")
        not_due = self.make_schedule("Not due")  # created just now: its first time hasn't come
        paused = self.make_schedule("Paused", is_active=False)
        self.make_due(due)
        self.make_due(paused)
        del not_due

        self.assertEqual([s["name"] for s in due_schedules(self.api)], ["Due"])

    def test_a_missing_source_is_recorded_as_failed_and_not_retried(self):
        schedule = self.make_schedule(source_path=str(self.tmp / "gone"))
        self.make_due(schedule)

        with self.assertRaises(FileNotFoundError):
            run_schedule(self.api, schedule)

        state = self.schedule_state(schedule)
        self.assertEqual(state["last_result"], "failed")
        self.assertFalse(state["is_due"])
        self.assertEqual(self.api.list_jobs(), [])

    def test_an_empty_folder_is_recorded_as_failed_too(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        schedule = self.make_schedule(source_path=str(empty))
        self.make_due(schedule)

        with self.assertRaises(ValueError):
            run_schedule(self.api, schedule)
        self.assertEqual(self.schedule_state(schedule)["last_result"], "failed")

    def test_a_run_where_files_fail_is_recorded_as_failed_with_a_count(self):
        schedule = self.make_schedule()
        shutil.rmtree(self.tmp / "provider0")
        shutil.rmtree(self.tmp / "provider1")  # no provider left to back up to

        result = run_schedule(self.api, schedule)

        self.assertEqual(result.status, "failed")
        self.assertEqual(self.schedule_state(schedule)["last_result"], "failed")


class ScheduleRunnerTests(ScheduleTestCase):
    def setUp(self):
        super().setUp()
        self.statuses, self.finished = [], []
        self.runner = ScheduleRunner(
            self.api,
            on_status=self.statuses.append,
            on_finished=lambda schedule, outcome: self.finished.append((schedule, outcome)),
        )
        self.addCleanup(self.runner.stop)

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.runner._running))

    def test_a_due_schedule_runs_when_the_runner_checks(self):
        schedule = self.make_schedule()
        self.make_due(schedule)

        self.runner.check_now()
        self.settle()

        self.assertEqual(len(self.finished), 1)
        self.assertEqual(self.finished[0][0]["id"], schedule["id"])
        self.assertEqual(self.finished[0][1].status, "completed")
        self.assertEqual(len(self.api.list_jobs()), 1)
        self.assertIn("Running scheduled backup 'Docs'…", self.statuses)
        self.assertTrue(any("'Docs' completed" in status for status in self.statuses))
        self.assertFalse(self.schedule_state(schedule)["is_due"])

    def test_several_due_schedules_run_one_after_another(self):
        for name in ("First", "Second"):
            self.make_due(self.make_schedule(name))

        self.runner.check_now()
        self.assertTrue(wait_until(lambda: len(self.finished) == 2))
        self.settle()

        self.assertEqual({s["name"] for s, _ in self.finished}, {"First", "Second"})
        self.assertEqual(len(self.api.list_jobs()), 2)

    def test_with_nothing_due_nothing_runs(self):
        self.make_schedule()  # created just now, so not due
        self.runner.check_now()
        self.settle()
        self.assertEqual(self.finished, [])
        self.assertEqual(self.api.list_jobs(), [])
        self.assertEqual(self.statuses[-1], "")

    def test_it_does_nothing_when_nobody_is_logged_in(self):
        runner = ScheduleRunner(ApiClient(api_url(self)), on_status=self.statuses.append)
        runner.check_now()
        self.assertFalse(runner._running)
        self.assertEqual(self.statuses, [])

    def test_it_waits_while_a_backup_the_user_started_is_running(self):
        release = threading.Event()
        self.addCleanup(release.set)
        run_in_background(lambda report: release.wait(10), on_progress=lambda *args: None)
        schedule = self.make_schedule()
        self.make_due(schedule)

        self.runner.check_now()
        self.assertFalse(self.runner._running)
        self.assertEqual(self.api.list_jobs(), [])

        release.set()
        from . import workers

        self.assertTrue(wait_until(lambda: not workers._active))
        self.runner.check_now()
        self.settle()
        self.assertEqual(len(self.api.list_jobs()), 1)

    def test_a_schedule_that_cannot_run_is_reported_once_and_not_retried_in_a_loop(self):
        schedule = self.make_schedule(source_path=str(self.tmp / "gone"))
        self.make_due(schedule)

        self.runner.check_now()
        self.settle()

        self.assertEqual(len(self.finished), 1)
        self.assertIn("Not a file or folder", self.finished[0][1])
        self.assertTrue(any("failed" in status for status in self.statuses))

        self.runner.check_now()
        self.settle()
        self.assertEqual(len(self.finished), 1)  # recorded, so it is no longer due

    def test_start_and_stop_control_the_timer(self):
        self.runner.start()
        self.assertTrue(self.runner._timer.isActive())
        self.runner.stop()
        self.assertFalse(self.runner._timer.isActive())


class AppSchedulingTests(ScheduleTestCase):
    def test_the_runner_only_runs_while_someone_is_logged_in(self):
        api = ApiClient(api_url(self))
        windows = AppWindows(api, run_schedules=True)
        self.addCleanup(set_unauthorized_handler, None)
        self.addCleanup(windows.main_window.runner.stop)
        self.addCleanup(lambda: [w.close() for w in windows._windows])
        windows.start()
        self.assertFalse(windows.main_window.runner._timer.isActive())

        windows.login_window.email_input.setText(EMAIL)
        windows.login_window.password_input.setText(PASSWORD)
        windows.login_window.login_button.click()
        self.assertTrue(windows.main_window.runner._timer.isActive())

        self.assertTrue(wait_until(lambda: windows.main_window.dashboard.providers_value.text() != "–"))
        windows.main_window.logout_button.click()
        self.assertTrue(wait_until(lambda: windows.login_window.isVisible()))
        self.assertFalse(windows.main_window.runner._timer.isActive())

    def test_scheduling_is_off_unless_asked_for(self):
        windows = AppWindows(ApiClient(api_url(self)))
        self.addCleanup(set_unauthorized_handler, None)
        self.addCleanup(lambda: [w.close() for w in windows._windows])
        windows.start()
        windows.show_main({"email": EMAIL, "full_name": ""})
        self.assertFalse(windows.main_window.runner._timer.isActive())


class SchedulesPageTests(ScheduleTestCase):
    def setUp(self):
        super().setUp()
        self.page = SchedulesPage(self.api)
        self.addCleanup(self.page.deleteLater)

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def load(self):
        self.page.refresh()
        self.settle()

    def cell(self, row, column):
        return self.page.table.item(row, column).text()

    def values(self, **overrides):
        return {
            "name": "Photos",
            "source_path": str(self.source),
            "frequency": "daily",
            "run_at": "03:30",
            "weekday": None,
            "is_active": True,
            "utc_offset_minutes": 0,
            **overrides,
        }

    def test_with_no_schedules_it_says_so(self):
        self.load()
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertFalse(self.page.empty_label.isHidden())

    def test_it_lists_schedules_with_readable_times_and_states(self):
        self.make_schedule("Daily docs")
        self.make_schedule("Weekly photos", frequency="weekly", weekday=0, run_at="03:30")
        paused = self.make_schedule("Paused one", is_active=False)
        due = self.make_schedule("Due one")
        self.make_due(due)
        del paused
        self.load()

        rows = {self.cell(row, 0): row for row in range(self.page.table.rowCount())}
        self.assertEqual(self.cell(rows["Daily docs"], 2), "Daily at 02:00")
        self.assertEqual(self.cell(rows["Weekly photos"], 2), "Weekly on Monday at 03:30")
        self.assertEqual(self.cell(rows["Daily docs"], 3), "Active")
        self.assertEqual(self.cell(rows["Paused one"], 3), "Paused")
        self.assertEqual(self.cell(rows["Due one"], 3), "Due now")
        self.assertEqual(self.cell(rows["Daily docs"], 4), "Never")
        self.assertEqual(self.cell(rows["Paused one"], 5), "—")
        self.assertTrue(self.page.empty_label.isHidden())

    def test_creating_a_schedule_adds_a_row(self):
        self.page.create_schedule(self.values())
        self.settle()
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.cell(0, 0), "Photos")
        self.assertEqual(self.api.list_schedules()[0]["run_at"], "03:30")

    def test_an_invalid_schedule_shows_the_servers_reason(self):
        self.page.create_schedule(self.values(frequency="weekly", weekday=None))
        self.settle()
        self.assertIn("Choose a day of the week", self.page.status_label.text())
        self.assertEqual(self.page.table.rowCount(), 0)

    def test_editing_pausing_resuming_and_deleting(self):
        schedule = self.make_schedule()
        self.load()

        self.page.update_schedule(schedule["id"], {"name": "Renamed", "run_at": "05:15"})
        self.settle()
        self.assertEqual((self.cell(0, 0), self.cell(0, 2)), ("Renamed", "Daily at 05:15"))

        self.page.set_active(schedule["id"], False)
        self.settle()
        self.assertEqual(self.cell(0, 3), "Paused")
        self.page.set_active(schedule["id"], True)
        self.settle()
        self.assertEqual(self.cell(0, 3), "Active")

        self.page.delete_schedule(schedule["id"])
        self.settle()
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertFalse(self.page.empty_label.isHidden())

    def test_buttons_follow_the_selection_and_the_toggle_label_follows_the_state(self):
        self.make_schedule("Active one")
        self.make_schedule("Paused one", is_active=False)
        self.load()
        for button in (self.page.edit_button, self.page.toggle_button, self.page.delete_button, self.page.run_button):
            self.assertFalse(button.isEnabled())

        rows = {self.cell(row, 0): row for row in range(2)}
        self.page.table.selectRow(rows["Active one"])
        self.assertTrue(self.page.run_button.isEnabled())
        self.assertEqual(self.page.toggle_button.text(), "Pause")
        self.page.table.selectRow(rows["Paused one"])
        self.assertEqual(self.page.toggle_button.text(), "Resume")

    def test_run_now_backs_up_immediately_and_shows_the_result(self):
        schedule = self.make_schedule()
        self.load()

        self.page.run_now(schedule["id"])
        self.settle()

        self.assertIn("completed", self.page.panel.result_label.text())
        self.assertEqual(len(self.api.list_jobs()), 1)
        self.assertIn("Completed", self.cell(0, 4))

    def test_run_now_with_a_missing_source_reports_it_and_records_the_failure(self):
        schedule = self.make_schedule(source_path=str(self.tmp / "gone"))
        self.load()

        self.page.run_now(schedule["id"])
        self.settle()

        self.assertIn("Not a file or folder", self.page.panel.result_label.text())
        self.assertIn("Failed", self.cell(0, 4))

    def test_delete_asks_first_and_can_be_cancelled(self):
        schedule = self.make_schedule()
        self.load()
        self.page.table.selectRow(0)
        with mock.patch("client.windows.schedules_page.QMessageBox.question") as ask:
            ask.return_value = mock.sentinel.no
            self.page.delete_selected()
        self.assertEqual(len(self.api.list_schedules()), 1)
        del schedule


class ScheduleDialogTests(SimpleTestCase):
    def setUp(self):
        self.dialog = ScheduleDialog()
        self.addCleanup(self.dialog.deleteLater)

    def test_the_day_only_matters_for_weekly_schedules(self):
        self.assertFalse(self.dialog.weekday_combo.isEnabled())
        self.dialog.frequency_combo.setCurrentIndex(self.dialog.frequency_combo.findData("weekly"))
        self.assertTrue(self.dialog.weekday_combo.isEnabled())

    def test_values_are_what_the_api_expects(self):
        self.dialog.name_input.setText("  Documents ")
        self.dialog.source_input.setText("C:/docs")
        self.assertEqual(
            self.dialog.values(),
            {
                "name": "Documents",
                "source_path": "C:/docs",
                "frequency": "daily",
                "run_at": "02:00",
                "weekday": None,
                "is_active": True,
                "utc_offset_minutes": local_utc_offset_minutes(),
            },
        )

        self.dialog.frequency_combo.setCurrentIndex(self.dialog.frequency_combo.findData("weekly"))
        self.dialog.weekday_combo.setCurrentIndex(4)
        self.assertEqual((self.dialog.values()["frequency"], self.dialog.values()["weekday"]), ("weekly", 4))

    def test_the_local_utc_offset_is_a_sane_number_of_minutes(self):
        self.assertTrue(-14 * 60 <= local_utc_offset_minutes() <= 14 * 60)

    def test_a_name_and_a_source_are_required(self):
        self.dialog._accept_if_valid()
        self.assertIn("name", self.dialog.error_label.text())

        self.dialog.name_input.setText("Docs")
        self.dialog._accept_if_valid()
        self.assertIn("folder or file", self.dialog.error_label.text())
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

        self.dialog.source_input.setText("C:/docs")
        self.dialog._accept_if_valid()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_editing_fills_the_form_from_the_schedule(self):
        schedule = {
            "name": "Weekly photos",
            "source_path": "D:/photos",
            "frequency": "weekly",
            "weekday": 5,
            "run_at": "21:45",
            "is_active": False,
        }
        dialog = ScheduleDialog(schedule)
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.windowTitle(), "Edit schedule")
        self.assertEqual(dialog.name_input.text(), "Weekly photos")
        self.assertEqual(dialog.frequency_combo.currentData(), "weekly")
        self.assertEqual(dialog.weekday_combo.currentText(), "Saturday")
        self.assertTrue(dialog.weekday_combo.isEnabled())
        self.assertEqual(dialog.time_input.time().toString("HH:mm"), "21:45")
        self.assertFalse(dialog.active_check.isChecked())
