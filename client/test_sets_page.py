import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from django.test import SimpleTestCase
from PySide6.QtWidgets import QDialog

from .backup_service import load_backups
from .test_gui import GuiTestCase, wait_until
from .windows.set_dialog import SetDialog
from .windows.sets_page import SetsPage, describe_last_backup, describe_places


class DescribeTests(SimpleTestCase):
    def test_one_place_is_shown_on_its_own_and_several_are_counted(self):
        self.assertEqual(describe_places(["C:/Docs"]), "C:/Docs")
        self.assertEqual(describe_places(["C:/Docs", "C:/Pics"]), "C:/Docs and 1 more")
        self.assertEqual(describe_places(["C:/a", "C:/b", "C:/c"]), "C:/a and 2 more")

    def test_a_long_path_is_shortened_to_its_last_parts(self):
        shown = describe_places(["C:/Users/me/Documents/Projects/Thesis"])
        self.assertTrue(shown.endswith("Projects/Thesis"))
        self.assertLess(len(shown), len("C:/Users/me/Documents/Projects/Thesis"))

    def test_a_set_that_never_ran_says_never(self):
        self.assertEqual(describe_last_backup({"last_run_at": None}), "Never")
        self.assertNotEqual(describe_last_backup({"last_run_at": "2026-10-26T12:00:00Z"}), "Never")


class SetDialogTests(SimpleTestCase):
    def setUp(self):
        self.dialog = SetDialog()
        self.addCleanup(self.dialog.deleteLater)

    def test_a_new_dialog_is_empty(self):
        self.assertEqual(self.dialog.windowTitle(), "New backup set")
        self.assertEqual(self.dialog.values(), {"name": "", "sources": []})

    def test_editing_starts_from_the_existing_set(self):
        dialog = SetDialog({"name": "Work", "sources": ["C:/Work", "C:/notes.txt"]})
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.windowTitle(), "Edit backup set")
        self.assertEqual(dialog.values(), {"name": "Work", "sources": ["C:/Work", "C:/notes.txt"]})

    def test_repeated_and_blank_places_are_not_added(self):
        self.dialog.add_paths(["C:/a", "C:/b", "C:/a", ""])
        self.dialog.add_paths(["C:/b", "C:/c"])
        self.assertEqual(self.dialog.sources(), ["C:/a", "C:/b", "C:/c"])

    def test_places_can_be_removed(self):
        self.dialog.add_paths(["C:/a", "C:/b"])
        self.dialog.sources_list.item(0).setSelected(True)
        self.dialog.remove_selected()
        self.assertEqual(self.dialog.sources(), ["C:/b"])

    def test_it_will_not_close_without_a_name(self):
        self.dialog.add_paths(["C:/a"])
        self.dialog._accept_if_valid()
        self.assertEqual(self.dialog.error_label.text(), "Give the set a name.")
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_it_will_not_close_without_a_place(self):
        self.dialog.name_input.setText("Work")
        self.dialog._accept_if_valid()
        self.assertEqual(self.dialog.error_label.text(), "Add at least one folder or file.")
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_a_name_and_a_place_are_enough_and_the_name_is_trimmed(self):
        self.dialog.name_input.setText("  Work  ")
        self.dialog.add_paths(["C:/a"])
        self.dialog._accept_if_valid()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.dialog.values(), {"name": "Work", "sources": ["C:/a"]})


class SetsPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = SetsPage(self.api)
        self.addCleanup(self.page.deleteLater)
        self.docs = self.make_folder("docs")
        (self.docs / "report.txt").write_text("report " * 100)

    def settle(self):
        self.assertTrue(wait_until(lambda: not (self.page._busy or self.page._backing_up)))

    def reload(self):
        self.page.refresh()
        self.settle()

    def cell(self, row, column):
        return self.page.table.item(row, column).text()

    def add_set(self, name="Docs", sources=None):
        self.page.create_set(name, sources or [str(self.docs)])
        self.settle()

    def connect_two_providers(self):
        self.connect_via_api("alpha")
        self.connect_via_api("beta")

    def test_with_no_sets_it_says_so_and_only_adding_is_possible(self):
        self.reload()
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertFalse(self.page.empty_label.isHidden())
        self.assertTrue(self.page.add_button.isEnabled())
        for button in (self.page.edit_button, self.page.delete_button, self.page.run_button):
            self.assertFalse(button.isEnabled())

    def test_a_created_set_appears_with_no_run_history(self):
        self.add_set("Docs", [str(self.docs), str(self.make_folder("pics"))])

        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.cell(0, 0), "Docs")
        self.assertIn("and 1 more", self.cell(0, 1))
        self.assertEqual(self.cell(0, 2), "Never")
        self.assertEqual(self.cell(0, 3), "—")
        self.assertIn(str(self.docs), self.page.table.item(0, 1).toolTip())
        self.assertTrue(self.page.empty_label.isHidden())

    def test_selecting_a_set_enables_the_buttons_that_need_one(self):
        self.add_set()
        self.page.table.selectRow(0)
        for button in (self.page.edit_button, self.page.delete_button, self.page.run_button):
            self.assertTrue(button.isEnabled())

    def test_a_set_can_be_changed_and_deleted(self):
        self.add_set("Docs")
        set_id = self.api.list_sets()[0]["id"]

        self.page.update_set(set_id, name="Renamed", sources=[str(self.docs), "C:/other"])
        self.settle()
        self.assertEqual(self.cell(0, 0), "Renamed")
        self.assertIn("and 1 more", self.cell(0, 1))

        self.page.delete_set(set_id)
        self.settle()
        self.assertEqual(self.page.table.rowCount(), 0)

    def test_a_server_refusal_is_shown_and_nothing_changes(self):
        self.add_set("Docs")
        self.add_set("docs")  # same name, different case
        self.assertIn("already have a set", self.page.status_label.text())
        self.assertEqual(self.page.table.rowCount(), 1)

    def test_backing_up_now_runs_the_set_and_shows_how_it_went(self):
        self.connect_two_providers()
        self.add_set()
        backup_set = self.api.list_sets()[0]

        self.page.run_now(backup_set)
        self.assertFalse(self.page.run_button.isEnabled())  # nothing else while it runs
        self.assertFalse(self.page.add_button.isEnabled())
        self.settle()

        self.assertEqual(self.cell(0, 3), "Completed")
        self.assertNotEqual(self.cell(0, 2), "Never")
        self.assertEqual(len(self.api.list_jobs()), 1)
        self.assertEqual(
            [v["original_filename"] for j in load_backups(self.api) for f in j["files"] for v in f["versions"]],
            ["report.txt"],
        )

    def test_a_second_request_while_one_is_running_is_ignored(self):
        self.connect_two_providers()
        self.add_set()
        backup_set = self.api.list_sets()[0]

        self.page.run_now(backup_set)
        self.page.run_now(backup_set)  # e.g. a double click
        self.settle()

        self.assertEqual(len(self.api.list_jobs()), 1)

    def test_a_set_pointing_at_a_missing_folder_fails_cleanly_and_can_be_fixed(self):
        self.connect_two_providers()
        self.add_set("Broken", [str(self.tmp / "vanished")])

        self.page.run_now(self.api.list_sets()[0])
        self.settle()

        self.assertEqual(self.cell(0, 3), "Failed")
        self.assertEqual(self.api.list_jobs(), [])
        self.assertTrue(self.page.add_button.isEnabled())  # the screen is usable again

        self.page.update_set(self.api.list_sets()[0]["id"], sources=[str(self.docs)])
        self.settle()
        self.page.run_now(self.api.list_sets()[0])
        self.settle()
        self.assertEqual(self.cell(0, 3), "Completed")

    def test_deleting_a_set_keeps_the_backups_it_made(self):
        self.connect_two_providers()
        self.add_set()
        self.page.run_now(self.api.list_sets()[0])
        self.settle()

        self.page.delete_set(self.api.list_sets()[0]["id"])
        self.settle()

        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertEqual(len(self.api.list_jobs()), 1)

    def test_the_same_set_stays_selected_even_when_the_list_reorders_around_it(self):
        self.add_set("Aaa")
        self.add_set("Bbb")
        self.page.table.selectRow(1)  # Bbb
        self.assertEqual(self.page.selected_set()["name"], "Bbb")

        # A new set that sorts above Bbb pushes it down a row; the selection must follow it.
        self.api.create_set("Abc", [str(self.docs)])
        self.reload()

        self.assertEqual([self.cell(row, 0) for row in range(3)], ["Aaa", "Abc", "Bbb"])
        self.assertEqual(self.page.selected_set()["name"], "Bbb")
