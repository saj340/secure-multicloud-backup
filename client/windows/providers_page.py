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
from client.formatting import format_timestamp
from client.workers import run_in_background

from .add_provider_dialog import AddProviderDialog

COLUMNS = ["Name", "Type", "Status", "Last checked"]
STATUS_STYLE = {
    "connected": ("Connected", "#1b7f3b"),
    "error": ("Error", "#b00020"),
    "disconnected": ("Disconnected", "#6b6b6b"),
}
MIN_PROVIDERS = 2


class ProvidersPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client
        self._busy = False
        self._providers = {}

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Cloud Providers")
        title.setFont(title_font)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: #b26a00;")
        self.warning_label.setFont(body_font)
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setFont(body_font)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.add_button = QPushButton("Add provider…")
        self.check_button = QPushButton("Check status")
        self.disconnect_button = QPushButton("Disconnect")
        self.refresh_button = QPushButton("Refresh")
        for button in (self.add_button, self.check_button, self.disconnect_button, self.refresh_button):
            button.setFont(body_font)
            button.setMinimumHeight(34)
        self.add_button.clicked.connect(self.add_provider)
        self.check_button.clicked.connect(self.check_selected)
        self.disconnect_button.clicked.connect(self.disconnect_selected)
        self.refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.disconnect_button)
        buttons.addStretch()
        buttons.addWidget(self.refresh_button)

        self.status_label = QLabel("")
        self.status_label.setFont(body_font)
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)

        self._update_buttons()

    # ------------------------------------------------------------ actions

    def refresh(self):
        self._start("Loading providers…", self.api_client.list_providers, self._loaded)

    def add_provider(self):
        self._start("Loading provider types…", self.api_client.list_provider_types, self._open_add_dialog)

    def connect_provider(self, provider_type, display_name, credentials):
        self._start(
            f"Connecting {display_name}…",
            lambda: self.api_client.connect_provider(provider_type, display_name, credentials),
            self._changed,
        )

    def check_selected(self):
        if (provider := self.selected_provider()) is not None:
            self.check_provider(provider["id"])

    def check_provider(self, provider_id):
        self._start(
            "Checking provider…", lambda: self.api_client.check_provider(provider_id), self._changed
        )

    def disconnect_selected(self):
        provider = self.selected_provider()
        if provider is None:
            return
        answer = QMessageBox.question(
            self,
            "Disconnect provider",
            f"Disconnect {provider['display_name']}?\n\n"
            "Its saved credentials are removed from the server, and backups already stored "
            "there lose that copy. Your other providers still hold enough to restore them, "
            "but with less protection until you back up again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.disconnect_provider(provider["id"])

    def disconnect_provider(self, provider_id):
        self._start(
            "Disconnecting…", lambda: self.api_client.disconnect_provider(provider_id), self._changed
        )

    def selected_provider(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        provider_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return self._providers.get(provider_id)

    # ---------------------------------------------------- state and results

    def _start(self, message, fn, on_success):
        self._busy = True
        self._update_buttons()
        self._show(message)
        run_in_background(fn, on_success, self._failed)

    def _loaded(self, providers):
        self._busy = False
        self._show("")
        self._populate(providers)
        self._update_buttons()

    def _changed(self, _result):
        self._busy = False
        self.refresh()

    def _failed(self, message):
        self._busy = False
        self._update_buttons()
        self._show(message, error=True)

    def _open_add_dialog(self, provider_types):
        self._busy = False
        self._show("")
        self._update_buttons()
        dialog = AddProviderDialog(provider_types, self)
        if dialog.type_combo.count() == 0:
            self._show("The server offers no provider types this app supports.", error=True)
        elif dialog.exec() == QDialog.DialogCode.Accepted:
            self.connect_provider(*dialog.values())

    def _populate(self, providers):
        self._providers = {p["id"]: p for p in providers}
        self.table.setRowCount(len(providers))
        for row, provider in enumerate(providers):
            status_text, status_color = STATUS_STYLE.get(
                provider["status"], (provider["status"], "#000000")
            )
            cells = [
                provider["display_name"],
                provider["provider_type_label"],
                status_text,
                format_timestamp(provider["last_checked_at"]),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, provider["id"])
                if column == 2:
                    item.setForeground(QBrush(QColor(status_color)))
                self.table.setItem(row, column, item)

        connected = sum(1 for p in providers if p["status"] == "connected")
        if connected < MIN_PROVIDERS:
            self.warning_label.setText(
                f"Backups need at least {MIN_PROVIDERS} connected providers, so a single "
                f"provider going down can't lose your files. You have {connected}."
            )
            self.warning_label.show()
        else:
            self.warning_label.hide()

    def _update_buttons(self):
        selected = self.selected_provider()
        actionable = (
            not self._busy and selected is not None and selected["status"] != "disconnected"
        )
        self.add_button.setEnabled(not self._busy)
        self.refresh_button.setEnabled(not self._busy)
        self.check_button.setEnabled(actionable)
        self.disconnect_button.setEnabled(actionable)

    def _show(self, message, error=False):
        self.status_label.setStyleSheet("color: #b00020;" if error else "color: #555555;")
        self.status_label.setText(message)
