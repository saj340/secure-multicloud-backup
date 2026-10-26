from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient
from client.backup_sets import run_set
from client.formatting import describe_status, format_timestamp, shorten_path
from client.workers import run_in_background

from .progress_panel import ERROR_STYLE, ProgressPanel
from .set_dialog import SetDialog

COLUMNS = ["Name", "Places", "Last backed up", "Result"]
MUTED_STYLE = "color: #555555;"


def describe_places(sources) -> str:
    """The first place, shortened, and how many more there are."""
    first = shorten_path(sources[0])
    return first if len(sources) == 1 else f"{first} and {len(sources) - 1} more"


def describe_last_backup(backup_set) -> str:
    return format_timestamp(backup_set["last_run_at"]) if backup_set["last_run_at"] else "Never"


class SetsPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False  # loading or changing the list of sets
        self._backing_up = False  # a "Back up now" is running
        self._sets = {}

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Backup Sets")
        title.setFont(title_font)

        hint = QLabel(
            "A set is a named group of folders and files you back up together. "
            "Back up now looks inside them at that moment, so files you added since last "
            "time are included. Each run is stored as its own dated backup."
        )
        hint.setFont(body_font)
        hint.setWordWrap(True)
        hint.setStyleSheet(MUTED_STYLE)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setFont(body_font)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column, name in enumerate(COLUMNS):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch
                if name == "Places"
                else QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.empty_label = QLabel("No sets yet. Use Add set to create one.")
        self.empty_label.setFont(body_font)
        self.empty_label.setStyleSheet(MUTED_STYLE)
        self.empty_label.hide()

        self.add_button = QPushButton("Add set…")
        self.edit_button = QPushButton("Edit…")
        self.delete_button = QPushButton("Delete")
        self.run_button = QPushButton("Back up now")
        self.refresh_button = QPushButton("Refresh")
        for button in (
            self.add_button,
            self.edit_button,
            self.delete_button,
            self.run_button,
            self.refresh_button,
        ):
            button.setFont(body_font)
            button.setMinimumHeight(34)
        self.add_button.clicked.connect(self.add_set)
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.run_button.clicked.connect(self.run_selected)
        self.refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.delete_button, self.run_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.refresh_button)

        self.status_label = QLabel("")
        self.status_label.setFont(body_font)
        self.status_label.setWordWrap(True)

        self.panel = ProgressPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.panel)

        self._update_buttons()

    # ------------------------------------------------------------ actions

    def refresh(self):
        self._start("Loading sets…", self.api_client.list_sets, self._loaded)

    def add_set(self):
        dialog = SetDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_set(**dialog.values())

    def create_set(self, name, sources):
        self._start("Saving…", lambda: self.api_client.create_set(name, sources), self._changed)

    def edit_selected(self):
        backup_set = self.selected_set()
        if backup_set is None:
            return
        dialog = SetDialog(backup_set, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_set(backup_set["id"], **dialog.values())

    def update_set(self, set_id, **fields):
        self._start(
            "Saving…", lambda: self.api_client.update_set(set_id, **fields), self._changed
        )

    def delete_selected(self):
        backup_set = self.selected_set()
        if backup_set is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete backup set",
            f"Delete the set '{backup_set['name']}'?\n\nBackups it already made are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_set(backup_set["id"])

    def delete_set(self, set_id):
        self._start("Deleting…", lambda: self.api_client.delete_set(set_id), self._changed)

    def run_selected(self):
        backup_set = self.selected_set()
        if backup_set is not None:
            self.run_now(backup_set)

    def run_now(self, backup_set):
        """Back up a set (a dict as the server returns it). Also used by the dashboard."""
        if self._backing_up:
            return
        self._backing_up = True
        self._update_buttons()
        self._show("", MUTED_STYLE)
        self.panel.start()
        run_in_background(
            lambda report: run_set(self.api_client, backup_set, progress=report),
            on_success=self._run_finished,
            on_error=self._run_failed,
            on_progress=self.panel.progress,
        )

    def selected_set(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        set_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return self._sets.get(set_id)

    # ------------------------------------------------- state and results

    def _start(self, message, fn, on_success):
        self._busy = True
        self._update_buttons()
        self._show(message, MUTED_STYLE)
        run_in_background(fn, on_success, self._failed)

    def _loaded(self, sets):
        self._busy = False
        self._show("", MUTED_STYLE)
        self._populate(sets)
        self._update_buttons()

    def _changed(self, _result):
        self._busy = False
        self.refresh()

    def _failed(self, message):
        self._busy = False
        self._show(message, ERROR_STYLE)
        self._update_buttons()

    def _run_finished(self, result):
        self._backing_up = False
        self.panel.show_backup_result(result)
        self.refresh()

    def _run_failed(self, message):
        self._backing_up = False
        self.panel.show_failure(message)
        self.refresh()  # the failure was recorded; show it

    def _populate(self, sets):
        selected = self.selected_set()
        keep = selected["id"] if selected else None
        self._sets = {s["id"]: s for s in sets}
        self.empty_label.setVisible(not sets)
        self.table.setRowCount(len(sets))
        for row, backup_set in enumerate(sets):
            result_text, result_color = (
                describe_status(backup_set["last_result"]) if backup_set["last_result"] else ("—", "#555555")
            )
            cells = [
                backup_set["name"],
                describe_places(backup_set["sources"]),
                describe_last_backup(backup_set),
                result_text,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, backup_set["id"])
                if column == 1:
                    item.setToolTip("\n".join(backup_set["sources"]))
                if column == 3:
                    item.setForeground(QBrush(QColor(result_color)))
                self.table.setItem(row, column, item)
            if backup_set["id"] == keep:
                self.table.selectRow(row)

    def _show(self, message, style):
        self.status_label.setStyleSheet(style)
        self.status_label.setText(message)

    def _update_buttons(self):
        idle = not (self._busy or self._backing_up)
        has_selection = self.selected_set() is not None
        self.add_button.setEnabled(idle)
        self.refresh_button.setEnabled(not self._busy)
        for button in (self.edit_button, self.delete_button, self.run_button):
            button.setEnabled(idle and has_selection)
