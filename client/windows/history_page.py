from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from client.api_client import ApiClient
from client.backup_service import load_backups, rerun_backup
from client.workers import run_in_background

from .backup_tree import BackupTree
from .progress_panel import ERROR_STYLE, ProgressPanel

MUTED_STYLE = "color: #555555;"


class HistoryPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Backup History")
        title.setFont(title_font)

        hint = QLabel(
            "Open a backup to see every version of each file. Select a backup to run it "
            "again, which stores a new version of each of its files. To get a file back, "
            "use Recover Files."
        )
        hint.setFont(body_font)
        hint.setWordWrap(True)
        hint.setStyleSheet(MUTED_STYLE)

        self.tree = BackupTree()
        self.tree.setFont(body_font)
        self.tree.itemSelectionChanged.connect(self._update_buttons)

        self.empty_label = QLabel("No backups yet. Use New Backup to create one.")
        self.empty_label.setFont(body_font)
        self.empty_label.setStyleSheet(MUTED_STYLE)
        self.empty_label.hide()

        self.run_button = QPushButton("Run again")
        self.refresh_button = QPushButton("Refresh")
        for button in (self.run_button, self.refresh_button):
            button.setFont(body_font)
            button.setMinimumHeight(34)
        self.run_button.clicked.connect(self.run_selected)
        self.refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
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
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.panel)

        self._update_buttons()

    def refresh(self):
        self._busy = True
        self._update_buttons()
        self._show("Loading backups…", MUTED_STYLE)
        run_in_background(lambda: load_backups(self.api_client), self._loaded, self._load_failed)

    def run_selected(self):
        job_id = self.tree.selected_job_id()
        if job_id is not None:
            self.run_again(job_id)

    def run_again(self, job_id):
        self._busy = True
        self._update_buttons()
        self._show("", MUTED_STYLE)
        self.panel.start()
        run_in_background(
            lambda report: rerun_backup(self.api_client, job_id, progress=report),
            on_success=self._run_finished,
            on_error=self._run_failed,
            on_progress=self.panel.progress,
        )

    def _loaded(self, jobs):
        self._busy = False
        self._show("", MUTED_STYLE)
        self.tree.load(jobs)
        self.empty_label.setVisible(not jobs)
        self._update_buttons()

    def _load_failed(self, message):
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
        self._update_buttons()

    def _show(self, message, style):
        self.status_label.setStyleSheet(style)
        self.status_label.setText(message)

    def _update_buttons(self):
        self.run_button.setEnabled(not self._busy and self.tree.selected_job_id() is not None)
        self.refresh_button.setEnabled(not self._busy)
