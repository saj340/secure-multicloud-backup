import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
from unittest import mock

from django.test import SimpleTestCase

from .api_client import ApiError
from .backup_service import rerun_backup, run_backup
from .test_gui import GuiTestCase, wait_until
from .windows.backup_tree import BackupTree
from .windows.history_page import HistoryPage
from .windows.recover_page import RecoverPage

SAVE_DIALOG = "client.windows.recover_page.QFileDialog.getSaveFileName"


def version(version_id, number, size, status):
    return {
        "id": version_id,
        "version_number": number,
        "original_filename": "report.txt",
        "original_size": size,
        "backup_time": "2026-09-20T10:00:00Z",
        "upload_status": status,
    }


SAMPLE_JOBS = [
    {
        "id": 1,
        "name": "Docs",
        "status": "completed",
        "created_at": "2026-09-20T10:00:00Z",
        "files": [
            {
                "id": 10,
                "original_path": "C:/docs/report.txt",
                "versions": [
                    version(100, 1, 2048, "completed"),
                    version(101, 2, 4096, "completed"),
                    version(102, 3, 4096, "failed"),
                ],
            },
            {
                "id": 11,
                "original_path": "C:/docs/broken.txt",
                "versions": [version(103, 1, 10, "failed")],
            },
        ],
    }
]


class BackupTreeTests(SimpleTestCase):
    def setUp(self):
        self.tree = BackupTree()
        self.tree.load(SAMPLE_JOBS)
        self.job = self.tree.topLevelItem(0)
        self.report = self.job.child(0)
        self.broken = self.job.child(1)

    def select(self, item):
        self.tree.setCurrentItem(item)

    def test_structure_and_columns(self):
        self.assertEqual(self.tree.topLevelItemCount(), 1)
        self.assertEqual(
            [self.job.text(i) for i in (0, 1, 2)], ["Docs", "Completed", "2 files"]
        )
        self.assertEqual(self.job.childCount(), 2)
        self.assertEqual(self.report.text(0), "report.txt")
        self.assertEqual(self.report.toolTip(0), "C:/docs/report.txt")
        self.assertEqual(self.report.text(2), "3 versions")
        self.assertEqual(self.broken.text(2), "1 version")

    def test_versions_are_listed_newest_first_with_status_and_size(self):
        names = [self.report.child(i).text(0) for i in range(3)]
        self.assertEqual(names, ["Version 3", "Version 2", "Version 1"])
        self.assertEqual(self.report.child(0).text(1), "Failed")
        self.assertEqual(self.report.child(1).text(1), "Completed")
        self.assertEqual(self.report.child(1).text(2), "4.0 KB")

    def test_nothing_selected_means_nothing_to_restore(self):
        self.tree.clearSelection()
        self.assertIsNone(self.tree.selected_data())
        self.assertIsNone(self.tree.selected_job_id())
        self.assertIsNone(self.tree.selected_recoverable_version())

    def test_any_row_selects_its_job_but_only_files_and_versions_restore(self):
        self.select(self.job)
        self.assertEqual(self.tree.selected_job_id(), 1)
        self.assertIsNone(self.tree.selected_recoverable_version())

        self.select(self.report.child(1))
        self.assertEqual(self.tree.selected_job_id(), 1)
        self.assertEqual(self.tree.selected_recoverable_version()["id"], 101)

    def test_selecting_a_file_restores_its_newest_completed_version(self):
        self.select(self.report)
        self.assertEqual(self.tree.selected_recoverable_version()["id"], 101)  # not failed v3

    def test_failed_versions_and_files_with_nothing_complete_are_not_restorable(self):
        self.select(self.report.child(0))  # Version 3, failed
        self.assertIsNone(self.tree.selected_recoverable_version())
        self.select(self.broken)  # only a failed version
        self.assertIsNone(self.tree.selected_recoverable_version())


class HistoryPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = HistoryPage(self.api)
        self.addCleanup(self.page.deleteLater)

        self.docs = self.tmp / "docs"
        self.docs.mkdir()
        (self.docs / "a.txt").write_text("alpha " * 100)
        (self.docs / "b.txt").write_text("bravo " * 100)
        for i in range(2):
            self.connect_via_api(f"provider{i}")

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def load(self):
        self.page.refresh()
        self.settle()

    def test_with_no_backups_it_says_so(self):
        self.load()
        self.assertEqual(self.page.tree.topLevelItemCount(), 0)
        self.assertFalse(self.page.empty_label.isHidden())

    def test_lists_backups_with_their_files_and_versions(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()

        job = self.page.tree.topLevelItem(0)
        self.assertEqual([job.text(i) for i in (0, 1, 2)], ["docs", "Completed", "2 files"])
        self.assertEqual(job.childCount(), 2)
        for row in range(2):
            file_item = job.child(row)
            self.assertEqual(file_item.childCount(), 1)
            self.assertEqual(file_item.child(0).text(0), "Version 1")
            self.assertEqual(file_item.child(0).text(1), "Completed")
        self.assertTrue(self.page.empty_label.isHidden())

    def test_run_again_stores_a_new_version_of_every_file(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()
        self.assertFalse(self.page.run_button.isEnabled())

        self.page.tree.setCurrentItem(self.page.tree.topLevelItem(0))
        self.assertTrue(self.page.run_button.isEnabled())
        self.page.run_selected()
        self.settle()

        self.assertIn("completed", self.page.panel.result_label.text())
        job = self.page.tree.topLevelItem(0)
        for row in range(2):
            versions = [job.child(row).child(i).text(0) for i in range(job.child(row).childCount())]
            self.assertEqual(versions, ["Version 2", "Version 1"])

    def test_selecting_a_version_also_allows_running_its_backup_again(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()
        self.page.tree.setCurrentItem(self.page.tree.topLevelItem(0).child(0).child(0))
        self.assertTrue(self.page.run_button.isEnabled())

    def test_running_again_with_providers_down_lists_the_failures(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()
        shutil.rmtree(self.tmp / "provider0")
        shutil.rmtree(self.tmp / "provider1")

        self.page.tree.setCurrentItem(self.page.tree.topLevelItem(0))
        self.page.run_selected()
        self.settle()

        panel = self.page.panel
        self.assertIn("2 of 2 files could not be backed up", panel.result_label.text())
        self.assertFalse(panel.failures_box.isHidden())
        self.assertIn("at least 2", panel.failures_box.toPlainText())

    def test_a_failed_load_shows_the_error(self):
        with mock.patch.object(self.api, "list_jobs", side_effect=ApiError("server is unhappy", 500)):
            self.load()
        self.assertEqual(self.page.status_label.text(), "server is unhappy")
        self.assertTrue(self.page.refresh_button.isEnabled())


class RecoverPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = RecoverPage(self.api)
        self.addCleanup(self.page.deleteLater)

        self.docs = self.tmp / "docs"
        self.docs.mkdir()
        self.report = self.docs / "report.txt"
        self.report.write_text("first draft " * 50)
        (self.docs / "data.bin").write_bytes(os.urandom(30_000))
        for i in range(3):
            self.connect_via_api(f"provider{i}")

        self.restored = self.tmp / "restored"
        self.restored.mkdir()

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def load(self):
        self.page.refresh()
        self.settle()

    def find_file(self, name):
        job = self.page.tree.topLevelItem(0)
        return next(job.child(i) for i in range(job.childCount()) if job.child(i).text(0) == name)

    def restore_via_button(self, item, destination):
        self.page.tree.setCurrentItem(item)
        self.assertTrue(self.page.restore_button.isEnabled())
        with mock.patch(SAVE_DIALOG, return_value=(str(destination), "")):
            self.page.restore_button.click()
        self.settle()

    def test_restoring_a_file_writes_it_back_and_records_the_recovery(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()

        target = self.restored / "report.txt"
        self.restore_via_button(self.find_file("report.txt"), target)

        self.assertEqual(target.read_bytes(), self.report.read_bytes())
        self.assertIn("Restored to", self.page.panel.result_label.text())
        self.assertEqual(self.page.history_table.rowCount(), 1)
        self.assertEqual(self.page.history_table.item(0, 0).text(), "report.txt")
        self.assertEqual(self.page.history_table.item(0, 2).text(), "Completed")

    def test_a_file_restores_its_newest_version_and_an_older_version_can_be_chosen(self):
        result = run_backup(self.api, "docs", [self.docs])
        old = self.report.read_bytes()
        self.report.write_text("second draft " * 50)
        new = self.report.read_bytes()
        rerun_backup(self.api, result.job_id)
        self.load()

        newest, oldest = self.restored / "newest.txt", self.restored / "oldest.txt"
        report_item = self.find_file("report.txt")
        self.restore_via_button(report_item, newest)
        self.restore_via_button(self.find_file("report.txt").child(1), oldest)  # "Version 1"

        self.assertEqual(newest.read_bytes(), new)
        self.assertEqual(oldest.read_bytes(), old)

    def test_a_backup_row_and_a_failed_version_cannot_be_restored(self):
        result = run_backup(self.api, "docs", [self.docs])
        shutil.rmtree(self.tmp / "provider0")
        shutil.rmtree(self.tmp / "provider1")
        rerun_backup(self.api, result.job_id)  # every file gets a failed Version 2
        self.load()

        self.page.tree.setCurrentItem(self.page.tree.topLevelItem(0))
        self.assertFalse(self.page.restore_button.isEnabled())

        report_item = self.find_file("report.txt")
        self.page.tree.setCurrentItem(report_item.child(0))  # Version 2, failed
        self.assertFalse(self.page.restore_button.isEnabled())
        self.page.tree.setCurrentItem(report_item)  # falls back to the newest good version
        self.assertTrue(self.page.restore_button.isEnabled())

    def test_cancelling_the_save_dialog_does_nothing(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()
        self.page.tree.setCurrentItem(self.find_file("report.txt"))

        with mock.patch(SAVE_DIALOG, return_value=("", "")):
            self.page.restore_button.click()

        self.assertFalse(self.page._busy)
        self.assertEqual(self.api.list_recoveries(), [])

    def test_losing_too_many_providers_reports_it_and_shows_the_failed_attempt(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()
        shutil.rmtree(self.tmp / "provider0")
        shutil.rmtree(self.tmp / "provider1")

        target = self.restored / "report.txt"
        self.restore_via_button(self.find_file("report.txt"), target)

        self.assertIn("Could not gather", self.page.panel.result_label.text())
        self.assertFalse(target.exists())
        self.assertEqual(self.page.history_table.rowCount(), 1)
        self.assertEqual(self.page.history_table.item(0, 2).text(), "Failed")

    def test_an_existing_file_is_not_overwritten_unless_confirmed(self):
        run_backup(self.api, "docs", [self.docs])
        self.load()
        self.page.tree.setCurrentItem(self.find_file("report.txt"))
        version_id = self.page.tree.selected_recoverable_version()["id"]

        target = self.restored / "report.txt"
        target.write_text("precious existing file")
        self.page.restore(version_id, str(target), overwrite=False)
        self.settle()

        self.assertIn("already exists", self.page.panel.result_label.text())
        self.assertEqual(target.read_text(), "precious existing file")
        self.assertEqual(self.api.list_recoveries(), [])
