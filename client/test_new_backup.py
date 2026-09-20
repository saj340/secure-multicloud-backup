import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

from . import backup_service
from .test_gui import GuiTestCase, wait_until
from .windows.new_backup_page import NewBackupPage


class NewBackupPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = NewBackupPage(self.api)
        self.addCleanup(self.page.deleteLater)

        self.docs = self.tmp / "docs"
        (self.docs / "sub").mkdir(parents=True)
        (self.docs / "a.txt").write_text("alpha " * 100)
        (self.docs / "sub" / "b.bin").write_bytes(os.urandom(20_000))

    def connect_providers(self, count):
        for i in range(count):
            self.connect_via_api(f"provider{i}")

    def load_readiness(self):
        self.page._readiness_known = False
        self.page.refresh()
        self.assertTrue(wait_until(lambda: self.page._readiness_known))

    def finish(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def test_it_will_not_start_without_two_connected_providers(self):
        self.connect_providers(1)
        self.page.add_paths([str(self.docs)])
        self.load_readiness()

        self.assertFalse(self.page.start_button.isEnabled())
        self.assertFalse(self.page.warning_label.isHidden())
        self.assertIn("at least 2", self.page.warning_label.text())

        self.connect_via_api("another")
        self.load_readiness()
        self.assertTrue(self.page.start_button.isEnabled())
        self.assertTrue(self.page.warning_label.isHidden())

    def test_the_start_button_needs_something_to_back_up(self):
        self.connect_providers(2)
        self.load_readiness()
        self.assertFalse(self.page.start_button.isEnabled())
        self.page.add_paths([str(self.docs)])
        self.assertTrue(self.page.start_button.isEnabled())

    def test_paths_are_deduplicated_and_can_be_removed_or_cleared(self):
        self.page.add_paths([str(self.docs), str(self.docs), str(self.docs / "a.txt")])
        self.assertEqual(len(self.page.paths()), 2)

        self.page.paths_list.item(0).setSelected(True)
        self.assertTrue(self.page.remove_button.isEnabled())
        self.page.remove_selected()
        self.assertEqual(self.page.paths(), [str(self.docs / "a.txt")])

        self.page.clear_selection()
        self.assertEqual(self.page.paths(), [])
        self.assertFalse(self.page.clear_button.isEnabled())

    def test_backing_up_from_the_page_end_to_end(self):
        self.connect_providers(3)
        self.page.add_paths([str(self.docs)])
        self.page.name_input.setText("docs")
        self.load_readiness()

        self.page.start_backup()
        self.finish()

        panel = self.page.panel
        self.assertIn("completed", panel.result_label.text())
        self.assertIn("2 files", panel.result_label.text())
        self.assertEqual((panel.progress_bar.value(), panel.progress_bar.maximum()), (2, 2))
        self.assertEqual(self.page.paths(), [])
        self.assertTrue(self.page.name_input.text().startswith("Backup "))

        jobs = self.api.list_jobs()
        self.assertEqual(
            [(j["name"], j["status"], j["file_count"]) for j in jobs], [("docs", "completed", 2)]
        )
        stored = [p for f in self.tmp.glob("provider*") for p in f.rglob("*.bin")]
        self.assertEqual(len(stored), 6)

    def test_files_that_fail_are_listed_and_the_selection_is_kept(self):
        self.connect_providers(2)
        self.page.add_paths([str(self.docs)])
        self.load_readiness()

        real = backup_service._backup_file

        def flaky(api, file, progress, index, total):
            if index == 1:
                raise OSError("simulated read error")
            return real(api, file, progress, index, total)

        with mock.patch.object(backup_service, "_backup_file", side_effect=flaky):
            self.page.start_backup()
            self.finish()

        panel = self.page.panel
        self.assertIn("1 of 2 files", panel.result_label.text())
        self.assertEqual(panel.stage_label.text(), "Finished with problems")
        self.assertFalse(panel.failures_box.isHidden())
        self.assertIn("simulated read error", panel.failures_box.toPlainText())
        self.assertEqual(self.page.paths(), [str(self.docs)])

    def test_a_missing_path_is_reported_and_creates_no_job(self):
        self.connect_providers(2)
        self.page.add_paths([str(self.tmp / "nope.txt")])
        self.load_readiness()

        self.page.start_backup()
        self.finish()

        self.assertIn("Not a file or folder", self.page.panel.result_label.text())
        self.assertEqual(self.api.list_jobs(), [])

    def test_starting_without_a_name_or_files_explains_why(self):
        self.page.name_input.setText("  ")
        self.page.start_backup()
        self.assertIn("Enter a name", self.page.panel.result_label.text())

        self.page.name_input.setText("docs")
        self.page.start_backup()
        self.assertIn("at least one file", self.page.panel.result_label.text())
        self.assertEqual(self.api.list_jobs(), [])

    def test_progress_updates_the_stage_the_current_file_and_the_bar(self):
        panel = self.page.panel
        panel.progress("protecting", str(self.docs / "a.txt"), 0, 2)
        self.assertTrue(panel.stage_label.text().startswith("Protecting"))
        self.assertEqual(panel.file_label.text(), "a.txt  (1 of 2)")

        panel.progress("processed", str(self.docs / "a.txt"), 1, 2)
        self.assertEqual((panel.progress_bar.value(), panel.progress_bar.maximum()), (1, 2))
