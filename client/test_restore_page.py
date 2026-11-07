import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase
from PySide6.QtWidgets import QMessageBox

from . import workers
from .api_client import ApiError
from .backup_service import RestoreSummary, restore_version, run_backup
from .test_gui import GuiTestCase, wait_until
from .windows.progress_panel import ERROR_STYLE, OK_STYLE, WARN_STYLE, ProgressPanel
from .windows.recover_page import RecoverPage


class RestoreSummaryPanelTests(SimpleTestCase):
    def setUp(self):
        from .test_gui import app  # noqa: F401  (makes sure a Qt application exists)

        self.panel = ProgressPanel()
        self.addCleanup(self.panel.deleteLater)

    def test_a_clean_restore_says_how_many_files_and_is_green(self):
        summary = RestoreSummary(restored=["a", "b", "c"])
        self.assertTrue(self.panel.show_restore_summary(summary))
        self.assertEqual(self.panel.result_label.text(), "Restored 3 files.")
        self.assertEqual(self.panel.result_label.styleSheet(), OK_STYLE)
        self.assertTrue(self.panel.failures_box.isHidden())

    def test_one_file_is_not_called_files(self):
        self.panel.show_restore_summary(RestoreSummary(restored=["a"]))
        self.assertEqual(self.panel.result_label.text(), "Restored 1 file.")

    def test_files_left_alone_are_mentioned(self):
        self.panel.show_restore_summary(RestoreSummary(restored=["a"], skipped=["b", "c"]))
        self.assertEqual(
            self.panel.result_label.text(), "Restored 1 file, 2 already there and left alone."
        )

    def test_some_files_failing_is_a_warning_that_lists_each_reason(self):
        summary = RestoreSummary(restored=["a"], failed={"C:/x/b.txt": "providers are down"})
        self.assertFalse(self.panel.show_restore_summary(summary))
        self.assertEqual(self.panel.result_label.styleSheet(), WARN_STYLE)
        self.assertIn("1 could not be restored", self.panel.result_label.text())
        self.assertFalse(self.panel.failures_box.isHidden())
        self.assertIn("C:/x/b.txt", self.panel.failures_box.toPlainText())
        self.assertIn("providers are down", self.panel.failures_box.toPlainText())

    def test_nothing_restored_at_all_is_an_error(self):
        self.panel.show_restore_summary(RestoreSummary(failed={"a": "x", "b": "y"}))
        self.assertEqual(self.panel.result_label.styleSheet(), ERROR_STYLE)
        self.assertIn("Restored 0 files", self.panel.result_label.text())

    def test_the_problem_list_goes_away_after_a_later_clean_restore(self):
        self.panel.show_restore_summary(RestoreSummary(failed={"a": "x"}))
        self.panel.show_restore_summary(RestoreSummary(restored=["a"]))
        self.assertTrue(self.panel.failures_box.isHidden())

    def test_a_cancelled_restore_is_a_neutral_notice_not_an_error(self):
        self.panel.show_notice("Restore cancelled. Nothing was changed.")
        self.assertNotEqual(self.panel.result_label.styleSheet(), ERROR_STYLE)
        self.assertIn("cancelled", self.panel.result_label.text())

    def test_progress_names_the_file_being_downloaded_and_counts_them(self):
        self.panel.progress("downloading", str(Path("C:/restored/docs/a.txt")), 1, 3)
        self.assertEqual(self.panel.file_label.text(), "a.txt  (2 of 3)")
        self.assertEqual(self.panel.progress_bar.maximum(), 3)


class RestoreWholeBackupPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        self.docs = self.make_folder("docs")
        (self.docs / "sub").mkdir()
        (self.docs / "a.txt").write_text("alpha " * 200)
        (self.docs / "sub" / "b.txt").write_text("bravo " * 300)
        (self.docs / "c.txt").write_text("charlie " * 100)
        self.job_id = run_backup(self.api, "Docs", [self.docs]).job_id
        self.dest = self.tmp / "restored"

        self.page = RecoverPage(self.api)
        self.addCleanup(self.page.deleteLater)
        self.reload()

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy and not workers._active))

    def reload(self):
        self.page.refresh()
        self.settle()

    def select_job(self):
        self.page.tree.setCurrentItem(self.page.tree.topLevelItem(0))

    def restored_text(self):
        return self.page.panel.result_label.text()

    def test_the_button_works_for_a_backup_or_anything_inside_it_but_not_for_nothing(self):
        self.assertFalse(self.page.restore_all_button.isEnabled())

        self.select_job()
        self.assertTrue(self.page.restore_all_button.isEnabled())

        self.page.tree.setCurrentItem(self.page.tree.topLevelItem(0).child(0))  # a file
        self.assertTrue(self.page.restore_all_button.isEnabled())

    def test_restoring_into_an_empty_folder_needs_no_question_and_brings_everything_back(self):
        with mock.patch.object(self.page, "_ask_about_existing") as ask:
            self.page.restore_whole(self.job_id, str(self.dest))
            self.settle()

        ask.assert_not_called()
        self.assertEqual(self.restored_text(), "Restored 3 files.")
        for relative, original in (
            ("a.txt", self.docs / "a.txt"),
            ("sub/b.txt", self.docs / "sub" / "b.txt"),
            ("c.txt", self.docs / "c.txt"),
        ):
            self.assertEqual((self.dest / relative).read_bytes(), original.read_bytes(), relative)
        self.assertEqual(self.page.history_table.rowCount(), 3)  # each file is on record
        self.assertFalse(self.page._busy)
        self.select_job()
        self.assertTrue(self.page.restore_all_button.isEnabled())  # the screen is usable again

    def test_when_files_exist_it_asks_how_many_and_keeping_them_leaves_them_untouched(self):
        (self.dest).mkdir()
        (self.dest / "a.txt").write_text("mine")

        with mock.patch.object(self.page, "_ask_about_existing", return_value=False) as ask:
            self.page.restore_whole(self.job_id, str(self.dest))
            self.settle()

        ask.assert_called_once_with(1, 3)
        self.assertEqual((self.dest / "a.txt").read_text(), "mine")
        self.assertEqual(self.restored_text(), "Restored 2 files, 1 already there and left alone.")

    def test_choosing_replace_overwrites_them(self):
        self.dest.mkdir()
        (self.dest / "a.txt").write_text("mine")

        with mock.patch.object(self.page, "_ask_about_existing", return_value=True):
            self.page.restore_whole(self.job_id, str(self.dest))
            self.settle()

        self.assertEqual((self.dest / "a.txt").read_text(), "alpha " * 200)
        self.assertEqual(self.restored_text(), "Restored 3 files.")

    def test_cancelling_changes_nothing_and_leaves_the_screen_usable(self):
        self.dest.mkdir()
        (self.dest / "a.txt").write_text("mine")

        with mock.patch.object(self.page, "_ask_about_existing", return_value=None):
            self.page.restore_whole(self.job_id, str(self.dest))
            self.settle()

        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["a.txt"])
        self.assertEqual((self.dest / "a.txt").read_text(), "mine")
        self.assertIn("cancelled", self.restored_text())
        self.assertEqual(self.api.list_recoveries(), [])  # not even started
        self.assertFalse(self.page._busy)

    def test_an_explicit_choice_is_used_without_asking(self):
        self.dest.mkdir()
        (self.dest / "a.txt").write_text("mine")

        with mock.patch.object(self.page, "_ask_about_existing") as ask:
            self.page.restore_whole(self.job_id, str(self.dest), overwrite=True)
            self.settle()

        ask.assert_not_called()
        self.assertEqual((self.dest / "a.txt").read_text(), "alpha " * 200)

    def test_one_file_failing_is_reported_by_name_while_the_rest_come_back(self):
        real = restore_version

        def fails_for_one(api, version_id, destination, **kwargs):
            if Path(destination).name == "c.txt":
                raise ApiError("a provider went away")
            return real(api, version_id, destination, **kwargs)

        with mock.patch("client.backup_service.restore_version", fails_for_one):
            self.page.restore_whole(self.job_id, str(self.dest))
            self.settle()

        self.assertEqual(
            self.restored_text(), "Restored 2 files, 1 could not be restored."
        )
        self.assertEqual(self.page.panel.result_label.styleSheet(), WARN_STYLE)
        self.assertIn("c.txt", self.page.panel.failures_box.toPlainText())
        self.assertIn("a provider went away", self.page.panel.failures_box.toPlainText())
        self.assertTrue((self.dest / "a.txt").exists())

    def test_with_every_provider_gone_it_says_so_clearly(self):
        for provider in self.api.list_providers():
            self.api.disconnect_provider(provider["id"])

        self.page.restore_whole(self.job_id, str(self.dest))
        self.settle()

        self.assertIn("Restored 0 files, 3 could not be restored", self.restored_text())
        self.assertEqual(self.page.panel.result_label.styleSheet(), ERROR_STYLE)

    def test_a_backup_that_no_longer_exists_shows_the_servers_message(self):
        self.page.restore_whole(self.job_id + 999, str(self.dest))
        self.settle()

        self.assertEqual(self.page.panel.result_label.styleSheet(), ERROR_STYLE)
        self.assertNotEqual(self.restored_text(), "")
        self.assertFalse(self.page._busy)

    def answer(self, which):
        """Press one of the question's buttons ("replace", "keep" or "cancel") and return what it decides."""
        shown = []

        def exec_and_capture(box):
            shown.append(box.text())
            return 0

        def clicked(box):
            if which == "cancel":
                return box.button(QMessageBox.StandardButton.Cancel)
            wanted = {"replace": "Replace them", "keep": "Keep the existing ones"}[which]
            return next(button for button in box.buttons() if button.text() == wanted)

        with mock.patch.object(QMessageBox, "exec", exec_and_capture), \
                mock.patch.object(QMessageBox, "clickedButton", clicked):
            decision = self.page._ask_about_existing(2, 5)
        return decision, shown[0]

    def test_the_question_says_how_many_files_are_affected(self):
        _decision, text = self.answer("keep")
        self.assertIn("2 of 5 files already exist", text)

    def test_each_button_of_the_question_means_what_it_says(self):
        self.assertIs(self.answer("replace")[0], True)
        self.assertIs(self.answer("keep")[0], False)
        self.assertIsNone(self.answer("cancel")[0])

    def test_closing_the_question_without_choosing_cancels_instead_of_replacing(self):
        with mock.patch.object(QMessageBox, "exec", return_value=0), \
                mock.patch.object(QMessageBox, "clickedButton", return_value=None):
            self.assertIsNone(self.page._ask_about_existing(1, 1))

    def test_the_button_asks_for_a_folder_and_does_nothing_if_it_is_cancelled(self):
        self.select_job()
        with mock.patch("client.windows.recover_page.QFileDialog.getExistingDirectory", return_value=""):
            self.page.restore_whole_selected()
        self.assertEqual(self.api.list_recoveries(), [])
        self.assertFalse(self.page._busy)

        with mock.patch(
            "client.windows.recover_page.QFileDialog.getExistingDirectory", return_value=str(self.dest)
        ):
            self.page.restore_whole_selected()
        self.settle()
        self.assertEqual(self.restored_text(), "Restored 3 files.")
