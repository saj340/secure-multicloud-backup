import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from .test_gui import GuiTestCase, wait_until
from .windows.dashboard_page import MAX_SET_SHORTCUTS, DashboardPage
from .windows.main_window import MainWindow


class DashboardSetsTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.clicked = []
        self.visited = []
        self.page = DashboardPage(
            self.api, on_navigate=self.visited.append, on_back_up=self.clicked.append
        )
        self.addCleanup(self.page.deleteLater)
        self.docs = self.make_folder("docs")

    def reload(self):
        self.page.refresh()
        self.assertTrue(wait_until(lambda: self.page.providers_value.text() != "–"))
        self.assertTrue(wait_until(lambda: not self.busy()))

    def busy(self):
        from . import workers

        return bool(workers._active)

    def add_sets(self, *names):
        for name in names:
            self.api.create_set(name, [str(self.docs)])

    def row_texts(self):
        texts = []
        for row in range(self.page.set_rows.count()):
            layout = self.page.set_rows.itemAt(row).layout()
            if layout is not None:
                texts.append(layout.itemAt(0).widget().text())
        return texts

    def test_with_no_saved_sets_the_section_is_not_shown(self):
        self.reload()
        self.assertTrue(self.page.sets_frame.isHidden())
        self.assertEqual(self.page.set_buttons, [])

    def test_each_saved_set_gets_a_row_with_a_button(self):
        self.add_sets("Docs", "Photos")
        self.reload()

        self.assertFalse(self.page.sets_frame.isHidden())
        self.assertEqual(self.row_texts(), ["Docs", "Photos"])
        self.assertEqual(len(self.page.set_buttons), 2)
        self.assertIsNone(self.page.show_all_button)

    def test_a_set_that_never_ran_says_so(self):
        self.add_sets("Docs")
        self.reload()
        when = self.page.set_rows.itemAt(0).layout().itemAt(1).widget().text()
        self.assertEqual(when, "Last backed up: Never")

    def test_the_button_hands_over_that_exact_set(self):
        self.add_sets("Docs", "Photos")
        self.reload()

        # Clicking each button in turn catches the classic loop bug where every button
        # ends up acting on the last set in the list.
        self.page.set_buttons[0].click()
        self.page.set_buttons[1].click()

        self.assertEqual([s["name"] for s in self.clicked], ["Docs", "Photos"])
        self.assertEqual(self.clicked[0]["sources"], [str(self.docs)])

    def test_only_the_first_few_are_shown_with_a_way_to_see_them_all(self):
        self.add_sets(*[f"Set {n}" for n in range(MAX_SET_SHORTCUTS + 2)])
        self.reload()

        self.assertEqual(len(self.page.set_buttons), MAX_SET_SHORTCUTS)
        self.assertEqual(self.page.show_all_button.text(), f"Show all {MAX_SET_SHORTCUTS + 2} sets")
        self.page.show_all_button.click()
        self.assertEqual(self.visited, ["Backup Sets"])

    def test_reloading_replaces_the_rows_instead_of_piling_up(self):
        self.add_sets("Docs")
        self.reload()
        self.reload()
        self.reload()
        self.assertEqual(self.row_texts(), ["Docs"])
        self.assertEqual(len(self.page.set_buttons), 1)

    def test_deleting_the_last_set_hides_the_section_again(self):
        self.add_sets("Docs")
        self.reload()
        self.api.delete_set(self.api.list_sets()[0]["id"])
        self.reload()
        self.assertTrue(self.page.sets_frame.isHidden())


class MainWindowBackUpNowTests(GuiTestCase):
    def test_the_dashboard_button_opens_the_sets_screen_and_runs_the_backup(self):
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        docs = self.make_folder("docs")
        (docs / "a.txt").write_text("hello " * 100)
        self.api.create_set("Docs", [str(docs)])

        window = MainWindow(self.api)
        self.addCleanup(window.deleteLater)
        window.set_user(self.api.me())
        self.assertTrue(wait_until(lambda: len(window.dashboard.set_buttons) == 1))

        window.dashboard.set_buttons[0].click()

        self.assertEqual(window.sidebar.currentItem().text(), "Backup Sets")
        self.assertTrue(wait_until(lambda: not (window.sets._busy or window.sets._backing_up)))
        self.assertEqual(len(self.api.list_jobs()), 1)
        self.assertTrue(wait_until(lambda: window.sets.table.rowCount() == 1))
        self.assertEqual(window.sets.table.item(0, 3).text(), "Completed")
