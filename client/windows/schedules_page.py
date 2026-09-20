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
from client.formatting import describe_status, format_timestamp, shorten_path
from client.scheduler import run_schedule
from client.workers import run_in_background

from .progress_panel import ERROR_STYLE, ProgressPanel
from .schedule_dialog import WEEKDAYS, ScheduleDialog

COLUMNS = ["Name", "Source", "Runs", "Status", "Last run", "Next run"]
MUTED_STYLE = "color: #555555;"


def describe_when(schedule) -> str:
    if schedule["frequency"] == "weekly" and schedule["weekday"] is not None:
        return f"Weekly on {WEEKDAYS[schedule['weekday']]} at {schedule['run_at']}"
    return f"Daily at {schedule['run_at']}"


def describe_state(schedule):
    """(text, color) for a schedule's status column."""
    if not schedule["is_active"]:
        return "Paused", "#6b6b6b"
    if schedule["is_due"]:
        return "Due now", "#b26a00"
    return "Active", "#1b7f3b"


def describe_last_run(schedule) -> str:
    if not schedule["last_run_at"]:
        return "Never"
    outcome = describe_status(schedule["last_result"])[0] if schedule["last_result"] else ""
    return f"{format_timestamp(schedule['last_run_at'])}  {outcome}".strip()


class SchedulesPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False
        self._schedules = {}

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Backup Schedules")
        title.setFont(title_font)

        hint = QLabel(
            "Scheduled backups run while this app is open and you are logged in. If the "
            "app was closed at the scheduled time, the backup runs when you next open it. "
            "Each run is stored as its own dated backup."
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
        for column, title_text in enumerate(COLUMNS):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch
                if title_text == "Source"
                else QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)  # keep both ends of long paths
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.empty_label = QLabel("No schedules yet. Use Add schedule to create one.")
        self.empty_label.setFont(body_font)
        self.empty_label.setStyleSheet(MUTED_STYLE)
        self.empty_label.hide()

        self.add_button = QPushButton("Add schedule…")
        self.edit_button = QPushButton("Edit…")
        self.toggle_button = QPushButton("Pause")
        self.delete_button = QPushButton("Delete")
        self.run_button = QPushButton("Run now")
        self.refresh_button = QPushButton("Refresh")
        for button in (
            self.add_button,
            self.edit_button,
            self.toggle_button,
            self.delete_button,
            self.run_button,
            self.refresh_button,
        ):
            button.setFont(body_font)
            button.setMinimumHeight(34)
        self.add_button.clicked.connect(self.add_schedule)
        self.edit_button.clicked.connect(self.edit_selected)
        self.toggle_button.clicked.connect(self.toggle_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.run_button.clicked.connect(self.run_selected)
        self.refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        for button in (
            self.add_button,
            self.edit_button,
            self.toggle_button,
            self.delete_button,
            self.run_button,
        ):
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
        self._start("Loading schedules…", self.api_client.list_schedules, self._loaded)

    def add_schedule(self):
        dialog = ScheduleDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_schedule(dialog.values())

    def create_schedule(self, values):
        self._start("Saving…", lambda: self.api_client.create_schedule(**values), self._changed)

    def edit_selected(self):
        schedule = self.selected_schedule()
        if schedule is None:
            return
        dialog = ScheduleDialog(schedule, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_schedule(schedule["id"], dialog.values())

    def update_schedule(self, schedule_id, values):
        self._start(
            "Saving…", lambda: self.api_client.update_schedule(schedule_id, **values), self._changed
        )

    def toggle_selected(self):
        schedule = self.selected_schedule()
        if schedule is not None:
            self.set_active(schedule["id"], not schedule["is_active"])

    def set_active(self, schedule_id, active):
        self.update_schedule(schedule_id, {"is_active": active})

    def delete_selected(self):
        schedule = self.selected_schedule()
        if schedule is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete schedule",
            f"Delete the schedule '{schedule['name']}'?\n\nBackups it already made are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_schedule(schedule["id"])

    def delete_schedule(self, schedule_id):
        self._start(
            "Deleting…", lambda: self.api_client.delete_schedule(schedule_id), self._changed
        )

    def run_selected(self):
        schedule = self.selected_schedule()
        if schedule is not None:
            self.run_now(schedule["id"])

    def run_now(self, schedule_id):
        schedule = self._schedules[schedule_id]
        self._busy = True
        self._update_buttons()
        self._show("", MUTED_STYLE)
        self.panel.start()
        run_in_background(
            lambda report: run_schedule(self.api_client, schedule, progress=report),
            on_success=self._run_finished,
            on_error=self._run_failed,
            on_progress=self.panel.progress,
        )

    def selected_schedule(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        schedule_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return self._schedules.get(schedule_id)

    # ------------------------------------------------- state and results

    def _start(self, message, fn, on_success):
        self._busy = True
        self._update_buttons()
        self._show(message, MUTED_STYLE)
        run_in_background(fn, on_success, self._failed)

    def _loaded(self, schedules):
        self._busy = False
        self._show("", MUTED_STYLE)
        self._populate(schedules)
        self._update_buttons()

    def _changed(self, _result):
        self._busy = False
        self.refresh()

    def _failed(self, message):
        self._busy = False
        self._show(message, ERROR_STYLE)
        self._update_buttons()

    def _run_finished(self, result):
        self._busy = False
        self.panel.show_backup_result(result)
        self.refresh()

    def _run_failed(self, message):
        self._busy = False
        self.panel.show_failure(message)
        self.refresh()  # the failed run was recorded; show it

    def _populate(self, schedules):
        self._schedules = {s["id"]: s for s in schedules}
        self.empty_label.setVisible(not schedules)
        self.table.setRowCount(len(schedules))
        for row, schedule in enumerate(schedules):
            state_text, state_color = describe_state(schedule)
            cells = [
                schedule["name"],
                shorten_path(schedule["source_path"]),
                describe_when(schedule),
                state_text,
                describe_last_run(schedule),
                format_timestamp(schedule["next_run_at"]) if schedule["is_active"] else "—",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, schedule["id"])
                if column == 1:
                    item.setToolTip(schedule["source_path"])
                if column == 3:
                    item.setForeground(QBrush(QColor(state_color)))
                self.table.setItem(row, column, item)

    def _show(self, message, style):
        self.status_label.setStyleSheet(style)
        self.status_label.setText(message)

    def _update_buttons(self):
        selected = self.selected_schedule()
        has_selection = selected is not None
        self.add_button.setEnabled(not self._busy)
        self.refresh_button.setEnabled(not self._busy)
        for button in (self.edit_button, self.toggle_button, self.delete_button, self.run_button):
            button.setEnabled(not self._busy and has_selection)
        self.toggle_button.setText("Pause" if selected is None or selected["is_active"] else "Resume")
