from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient
from client.workers import long_task_running, run_in_background

from .dashboard_page import DashboardPage
from .new_backup_page import NewBackupPage
from .providers_page import ProvidersPage


class MainWindow(QWidget):
    def __init__(self, api_client: ApiClient, on_logout=None):
        super().__init__()
        self.api_client = api_client
        self.on_logout = on_logout

        self.setWindowTitle("Secure Multi-Cloud Backup")
        self.resize(980, 620)
        self.setMinimumSize(820, 520)

        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        app_title = QLabel("Secure Multi-Cloud Backup")
        app_title.setFont(title_font)

        self.user_label = QLabel("")
        self.user_label.setFont(body_font)
        self.user_label.setStyleSheet("color: #555555;")

        self.logout_button = QPushButton("Log out")
        self.logout_button.setFont(body_font)
        self.logout_button.clicked.connect(self._logout)

        header = QHBoxLayout()
        header.addWidget(app_title)
        header.addStretch()
        header.addWidget(self.user_label)
        header.addWidget(self.logout_button)

        self.dashboard = DashboardPage(api_client)
        self.new_backup = NewBackupPage(api_client)
        self.providers = ProvidersPage(api_client)
        self._pages = [
            ("Dashboard", self.dashboard),
            ("New Backup", self.new_backup),
            ("Cloud Providers", self.providers),
        ]

        self.pages = QStackedWidget()
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(200)
        self.sidebar.setFont(body_font)
        self.sidebar.setStyleSheet("QListWidget::item { padding: 12px 10px; }")
        for name, page in self._pages:
            self.sidebar.addItem(name)
            self.pages.addWidget(page)
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self._show_page)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self.sidebar)
        body.addWidget(self.pages, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addLayout(body, 1)

    def set_user(self, user):
        self.user_label.setText(user["email"])
        self.dashboard.set_user(user)
        if self.sidebar.currentRow() == 0:
            self.dashboard.refresh()
        else:
            self.sidebar.setCurrentRow(0)

    def _show_page(self, index):
        self.pages.setCurrentIndex(index)
        self._pages[index][1].refresh()

    def closeEvent(self, event):
        if self._confirm_interrupting_task("Closing the app"):
            event.accept()
        else:
            event.ignore()

    def _confirm_interrupting_task(self, action):
        """Stopping a backup part-way leaves it incomplete, so ask before doing it."""
        if not long_task_running():
            return True
        answer = QMessageBox.question(
            self,
            "Backup in progress",
            f"A backup or restore is still running. {action} now will stop it part-way "
            "and leave it incomplete.\n\nDo you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _logout(self):
        if not self._confirm_interrupting_task("Logging out"):
            return
        self.logout_button.setEnabled(False)

        def done(_result):
            self.logout_button.setEnabled(True)
            if self.on_logout:
                self.on_logout()

        # Sign out on this side even if the server can't be reached.
        run_in_background(self.api_client.logout, done, done)
