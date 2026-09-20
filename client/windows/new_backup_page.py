from datetime import datetime

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient
from client.backup_service import run_backup
from client.workers import run_in_background

from .progress_panel import ERROR_STYLE, WARN_STYLE, ProgressPanel

MIN_PROVIDERS = 2


def default_backup_name():
    return f"Backup {datetime.now():%Y-%m-%d %H:%M}"


class NewBackupPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False
        self._ready = False
        self._readiness_known = False

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("New Backup")
        title.setFont(title_font)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet(WARN_STYLE)
        self.warning_label.setFont(body_font)
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()

        name_label = QLabel("Backup name")
        name_label.setFont(body_font)
        self.name_input = QLineEdit(default_backup_name())
        self.name_input.setFont(body_font)
        self.name_input.setMinimumHeight(34)

        list_label = QLabel("Files and folders to back up")
        list_label.setFont(body_font)
        self.paths_list = QListWidget()
        self.paths_list.setFont(body_font)
        self.paths_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self.add_files_button = QPushButton("Add files…")
        self.add_folder_button = QPushButton("Add folder…")
        self.remove_button = QPushButton("Remove selected")
        self.clear_button = QPushButton("Clear")
        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_button,
            self.clear_button,
        ):
            button.setFont(body_font)
            button.setMinimumHeight(34)
        self.add_files_button.clicked.connect(self.add_files)
        self.add_folder_button.clicked.connect(self.add_folder)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_selection)
        self.paths_list.itemSelectionChanged.connect(self._update_controls)

        selection_buttons = QHBoxLayout()
        selection_buttons.addWidget(self.add_files_button)
        selection_buttons.addWidget(self.add_folder_button)
        selection_buttons.addStretch()
        selection_buttons.addWidget(self.remove_button)
        selection_buttons.addWidget(self.clear_button)

        self.start_button = QPushButton("Start backup")
        self.start_button.setFont(body_font)
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self.start_backup)

        self.panel = ProgressPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.warning_label)
        layout.addWidget(name_label)
        layout.addWidget(self.name_input)
        layout.addWidget(list_label)
        layout.addWidget(self.paths_list, 1)
        layout.addLayout(selection_buttons)
        layout.addWidget(self.start_button)
        layout.addWidget(self.panel)

        self._update_controls()

    # ---------------------------------------------------------- selection

    def paths(self):
        return [self.paths_list.item(row).text() for row in range(self.paths_list.count())]

    def add_paths(self, new_paths):
        existing = set(self.paths())
        for path in new_paths:
            if path not in existing:
                self.paths_list.addItem(path)
                existing.add(path)
        self._update_controls()

    def add_files(self):
        files, _filter = QFileDialog.getOpenFileNames(self, "Choose files to back up")
        self.add_paths(files)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to back up")
        if folder:
            self.add_paths([folder])

    def remove_selected(self):
        for item in self.paths_list.selectedItems():
            self.paths_list.takeItem(self.paths_list.row(item))
        self._update_controls()

    def clear_selection(self):
        self.paths_list.clear()
        self._update_controls()

    # ---------------------------------------------------------- readiness

    def refresh(self):
        run_in_background(self._count_connected, self._readiness_loaded, self._readiness_failed)

    def _count_connected(self):
        return sum(1 for p in self.api_client.list_providers() if p["status"] == "connected")

    def _readiness_loaded(self, connected):
        self._readiness_known = True
        self._ready = connected >= MIN_PROVIDERS
        if self._ready:
            self.warning_label.hide()
        else:
            self.warning_label.setText(
                f"Connect at least {MIN_PROVIDERS} cloud providers (under Cloud Providers) "
                f"before backing up. You have {connected} connected."
            )
            self.warning_label.show()
        self._update_controls()

    def _readiness_failed(self, message):
        self._readiness_known = True
        self._ready = False
        self.warning_label.setText(message)
        self.warning_label.show()
        self._update_controls()

    # ------------------------------------------------------------- backup

    def start_backup(self):
        name = self.name_input.text().strip()
        paths = self.paths()
        if not name:
            return self.panel.show_message("Enter a name for this backup.", ERROR_STYLE)
        if not paths:
            return self.panel.show_message("Add at least one file or folder.", ERROR_STYLE)

        self._busy = True
        self._update_controls()
        self.panel.start()
        run_in_background(
            lambda report: run_backup(self.api_client, name, paths, progress=report),
            on_success=self._finished,
            on_error=self._failed,
            on_progress=self.panel.progress,
        )

    def _finished(self, result):
        self._busy = False
        if self.panel.show_backup_result(result):
            self.paths_list.clear()
            self.name_input.setText(default_backup_name())
        self._update_controls()

    def _failed(self, message):
        self._busy = False
        self.panel.show_failure(message)
        self._update_controls()

    def _update_controls(self):
        idle = not self._busy
        self.name_input.setEnabled(idle)
        self.add_files_button.setEnabled(idle)
        self.add_folder_button.setEnabled(idle)
        self.remove_button.setEnabled(idle and bool(self.paths_list.selectedItems()))
        self.clear_button.setEnabled(idle and self.paths_list.count() > 0)
        self.start_button.setEnabled(idle and self._ready and self.paths_list.count() > 0)
