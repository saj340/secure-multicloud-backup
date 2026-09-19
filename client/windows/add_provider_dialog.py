from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# What each provider type needs from the user: (credential key, label, kind).
# kind is "folder" (with a Browse button), "text", or "secret" (hidden).
# Supporting a new provider type in the client is one new entry here.
CREDENTIAL_FIELDS = {
    "local_folder": [("path", "Folder", "folder")],
}

FIXED_ROWS = 2  # provider type and display name; credential rows follow


class AddProviderDialog(QDialog):
    def __init__(self, provider_types, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add cloud provider")
        self.setMinimumWidth(520)

        self._inputs = {}

        self.type_combo = QComboBox()
        for provider_type in provider_types:
            if provider_type["value"] in CREDENTIAL_FIELDS:
                self.type_combo.addItem(provider_type["label"], provider_type["value"])

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. My backup drive")

        self.form = QFormLayout()
        self.form.setVerticalSpacing(12)
        self.form.addRow("Provider type", self.type_combo)
        self.form.addRow("Display name", self.name_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addLayout(self.form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

        self.type_combo.currentIndexChanged.connect(self._rebuild_fields)
        self._rebuild_fields()

    def _rebuild_fields(self):
        while self.form.rowCount() > FIXED_ROWS:
            self.form.removeRow(FIXED_ROWS)
        self._inputs = {}

        for key, label, kind in CREDENTIAL_FIELDS.get(self.type_combo.currentData(), []):
            edit = QLineEdit()
            self._inputs[key] = edit
            if kind == "secret":
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            if kind == "folder":
                browse = QPushButton("Browse…")
                browse.clicked.connect(lambda _checked=False, target=edit: self._browse(target))
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(edit)
                row_layout.addWidget(browse)
                self.form.addRow(label, row)
            else:
                self.form.addRow(label, edit)

    def _browse(self, target: QLineEdit):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if folder:
            target.setText(folder)

    def values(self):
        """(provider_type, display_name, credentials) as entered."""
        credentials = {key: edit.text().strip() for key, edit in self._inputs.items()}
        return self.type_combo.currentData(), self.name_input.text().strip(), credentials

    def _accept_if_valid(self):
        provider_type, name, credentials = self.values()
        if provider_type is None:
            self.error_label.setText("No provider types are available.")
        elif not name:
            self.error_label.setText("Enter a display name.")
        elif not all(credentials.values()):
            self.error_label.setText("Fill in every field.")
        else:
            self.accept()
