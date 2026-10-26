import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from .backup_service import run_backup
from .formatting import format_size
from .test_gui import GuiTestCase, wait_until
from .windows.providers_page import COLUMNS, STORED_COLUMN, ProvidersPage


def bytes_on_disk(folder):
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


class StoredColumnTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.folders = {name: self.connect_via_api(name) for name in ("alpha", "beta")}
        self.page = ProvidersPage(self.api)
        self.addCleanup(self.page.deleteLater)

    def reload(self):
        self.page.refresh()
        self.assertTrue(wait_until(lambda: self.page.table.rowCount() == 2))
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def stored_text(self, name):
        table = self.page.table
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == name:
                return table.item(row, STORED_COLUMN).text()

    def test_the_column_is_there_and_nothing_stored_reads_zero(self):
        self.reload()
        self.assertEqual(COLUMNS[STORED_COLUMN], "Stored")
        self.assertEqual(self.stored_text("alpha"), "0 B")
        self.assertEqual(self.stored_text("beta"), "0 B")

    def test_after_a_backup_each_provider_shows_what_it_really_holds(self):
        document = self.tmp / "report.txt"
        document.write_text("quarterly figures " * 4000)
        self.assertEqual(run_backup(self.api, "docs", [document]).status, "completed")

        self.reload()

        for name, folder in self.folders.items():
            self.assertEqual(self.stored_text(name), format_size(bytes_on_disk(folder)))
        self.assertNotEqual(self.stored_text("alpha"), "0 B")

    def test_the_sizes_are_lined_up_on_the_right(self):
        self.reload()
        alignment = self.page.table.item(0, STORED_COLUMN).textAlignment()
        self.assertTrue(alignment & Qt.AlignmentFlag.AlignRight)
