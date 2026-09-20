from pathlib import Path

from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
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
from client.backup_service import load_backups, restore_version
from client.formatting import describe_status, format_timestamp
from client.workers import run_in_background

from .backup_tree import BackupTree
from .progress_panel import ERROR_STYLE, ProgressPanel

MUTED_STYLE = "color: #555555;"
HISTORY_COLUMNS = ["File", "Version", "Status", "Started", "Finished"]


class RecoverPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Recover Files")
        title.setFont(title_font)

        hint = QLabel(
            "Choose a file (its newest version is used) or a specific version, then restore "
            "it. The pieces are gathered from your providers, checked, and decrypted on "
            "this device."
        )
        hint.setFont(body_font)
        hint.setWordWrap(True)
        hint.setStyleSheet(MUTED_STYLE)

        self.tree = BackupTree()
        self.tree.setFont(body_font)
        self.tree.itemSelectionChanged.connect(self._update_buttons)

        self.empty_label = QLabel("Nothing to recover yet. Back something up first.")
        self.empty_label.setFont(body_font)
        self.empty_label.setStyleSheet(MUTED_STYLE)
        self.empty_label.hide()

        self.restore_button = QPushButton("Restore selected…")
        self.refresh_button = QPushButton("Refresh")
        for button in (self.restore_button, self.refresh_button):
            button.setFont(body_font)
            button.setMinimumHeight(34)
        self.restore_button.clicked.connect(self.restore_selected)
        self.refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        buttons.addWidget(self.restore_button)
        buttons.addStretch()
        buttons.addWidget(self.refresh_button)

        self.status_label = QLabel("")
        self.status_label.setFont(body_font)
        self.status_label.setWordWrap(True)

        self.panel = ProgressPanel()

        history_label = QLabel("Recovery history")
        history_label.setFont(body_font)
        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table.setFont(body_font)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.tree, 3)
        layout.addWidget(self.empty_label)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.panel)
        layout.addWidget(history_label)
        layout.addWidget(self.history_table, 2)

        self._update_buttons()

    def refresh(self):
        self._busy = True
        self._update_buttons()
        self._show("Loading…", MUTED_STYLE)
        run_in_background(self._fetch, self._loaded, self._load_failed)

    def _fetch(self):
        return load_backups(self.api_client), self.api_client.list_recoveries()

    def restore_selected(self):
        version = self.tree.selected_recoverable_version()
        if version is None:
            return
        suggested = str(Path.home() / version["original_filename"])
        # The save dialog asks before replacing an existing file.
        destination, _filter = QFileDialog.getSaveFileName(self, "Restore file as", suggested)
        if destination:
            self.restore(version["id"], destination, overwrite=True)

    def restore(self, version_id, destination, overwrite=False):
        self._busy = True
        self._update_buttons()
        self._show("", MUTED_STYLE)
        self.panel.start()
        run_in_background(
            lambda report: restore_version(
                self.api_client, version_id, destination, overwrite=overwrite, progress=report
            ),
            on_success=self._restored,
            on_error=self._restore_failed,
            on_progress=self.panel.progress,
        )

    def _loaded(self, data):
        jobs, recoveries = data
        self._busy = False
        self._show("", MUTED_STYLE)
        self.tree.load(jobs)
        self.empty_label.setVisible(not jobs)
        self._populate_history(recoveries)
        self._update_buttons()

    def _load_failed(self, message):
        self._busy = False
        self._show(message, ERROR_STYLE)
        self._update_buttons()

    def _restored(self, destination):
        self._busy = False
        self.panel.finish_ok(f"Restored to {destination}")
        self.refresh()

    def _restore_failed(self, message):
        self._busy = False
        self.panel.show_failure(message)
        self.refresh()  # a failed attempt is recorded server-side; show it in the history

    def _populate_history(self, recoveries):
        self.history_table.setRowCount(len(recoveries))
        for row, operation in enumerate(recoveries):
            status_text, status_color = describe_status(operation["status"])
            cells = [
                operation["original_filename"],
                f"Version {operation['version_number']}",
                status_text,
                format_timestamp(operation["started_at"]),
                format_timestamp(operation["completed_at"]),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 2:
                    item.setForeground(QBrush(QColor(status_color)))
                    if operation["error_message"]:
                        item.setToolTip(operation["error_message"])
                self.history_table.setItem(row, column, item)

    def _show(self, message, style):
        self.status_label.setStyleSheet(style)
        self.status_label.setText(message)

    def _update_buttons(self):
        self.restore_button.setEnabled(
            not self._busy and self.tree.selected_recoverable_version() is not None
        )
        self.refresh_button.setEnabled(not self._busy)
