from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient
from client.formatting import format_timestamp
from client.workers import run_in_background

from .progress_panel import ERROR_STYLE

COLUMNS = ["When", "Level", "Event", "Details"]
MUTED_STYLE = "color: #555555;"
HISTORY_LIMIT = 200

EVENT_LABELS = {
    "registered": "Account created",
    "login": "Signed in",
    "logout": "Signed out",
    "login_failed": "Failed sign-in",
    "password_changed": "Password changed",
    "password_reset": "Password reset",
    "provider_connected": "Provider connected",
    "provider_disconnected": "Provider disconnected",
    "provider_unreachable": "Provider unreachable",
    "provider_recovered": "Provider reachable again",
    "backup_run_started": "Backup started",
    "backup_run_completed": "Backup finished",
    "backup_run_failed": "Backup failed",
    "file_version_failed": "File not backed up",
    "recovery_completed": "File restored",
    "recovery_failed": "Restore failed",
    "provider_reconnected": "Provider reconnected",
    "scheduled_backup_completed": "Scheduled backup finished",
    "scheduled_backup_failed": "Scheduled backup failed",
}

LEVEL_DISPLAY = {
    "info": ("Info", "#555555"),
    "warning": ("Warning", "#b26a00"),
    "error": ("Error", "#b00020"),
}


def describe_event(action: str) -> str:
    # An event added on the server later still reads sensibly before this table knows it.
    return EVENT_LABELS.get(action, action.replace("_", " ").capitalize())


def describe_level(level: str):
    return LEVEL_DISPLAY.get(level, (level.capitalize(), "#555555"))


class ActivityPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False
        self._entries = []

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Activity")
        title.setFont(title_font)

        hint = QLabel(
            f"The most recent {HISTORY_LIMIT} things that happened on your account: "
            "sign-ins, backups, restores and changes to your cloud providers."
        )
        hint.setFont(body_font)
        hint.setWordWrap(True)
        hint.setStyleSheet(MUTED_STYLE)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setFont(body_font)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column, title_text in enumerate(COLUMNS):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch
                if title_text == "Details"
                else QHeaderView.ResizeMode.ResizeToContents,
            )

        self.empty_label = QLabel("Nothing to show yet.")
        self.empty_label.setFont(body_font)
        self.empty_label.setStyleSheet(MUTED_STYLE)
        self.empty_label.hide()

        self.problems_only = QCheckBox("Show warnings and errors only")
        self.problems_only.setFont(body_font)
        self.problems_only.toggled.connect(self._populate)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setFont(body_font)
        self.refresh_button.setMinimumHeight(34)
        self.refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.addWidget(self.problems_only)
        controls.addStretch()
        controls.addWidget(self.refresh_button)

        self.status_label = QLabel("")
        self.status_label.setFont(body_font)
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(controls)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.status_label)

    def refresh(self):
        self._busy = True
        self.refresh_button.setEnabled(False)
        self._show("Loading…", MUTED_STYLE)
        run_in_background(
            lambda: self.api_client.list_activity(HISTORY_LIMIT), self._loaded, self._failed
        )

    def _loaded(self, entries):
        self._busy = False
        self.refresh_button.setEnabled(True)
        self._show("", MUTED_STYLE)
        self._entries = entries
        self._populate()

    def _failed(self, message):
        self._busy = False
        self.refresh_button.setEnabled(True)
        self._show(message, ERROR_STYLE)

    def _populate(self, *_):
        shown = [
            entry
            for entry in self._entries
            if not self.problems_only.isChecked() or entry["level"] != "info"
        ]
        self.empty_label.setVisible(not shown)
        self.empty_label.setText(
            "No warnings or errors." if self._entries and not shown else "Nothing to show yet."
        )
        self.table.setRowCount(len(shown))
        for row, entry in enumerate(shown):
            level_text, level_color = describe_level(entry["level"])
            cells = [
                format_timestamp(entry["created_at"]),
                level_text,
                describe_event(entry["action"]),
                entry["description"],
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setForeground(QBrush(QColor(level_color)))
                if column == 3:
                    item.setToolTip(text)  # a long message is cut off in the table
                self.table.setItem(row, column, item)

    def _show(self, message, style):
        self.status_label.setStyleSheet(style)
        self.status_label.setText(message)
