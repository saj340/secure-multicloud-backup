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

# What each provider type needs from the user: (credential key, label, kind,
# required). kind is "folder" (with a Browse button), "text", or "secret"
# (hidden). Supporting a new provider type in the client is one new entry here.
CREDENTIAL_FIELDS = {
    "local_folder": [("path", "Folder", "folder", True)],
    "aws_s3": [
        ("bucket", "Bucket", "text", True),
        ("region", "Region", "text", True),
        ("access_key_id", "Access key ID", "text", True),
        ("secret_access_key", "Secret access key", "secret", True),
    ],
    "s3_compatible": [
        ("endpoint_url", "Endpoint URL", "text", True),
        ("bucket", "Bucket", "text", True),
        ("region", "Region (optional)", "text", False),
        ("access_key_id", "Access key ID", "text", True),
        ("secret_access_key", "Secret access key", "secret", True),
    ],
}

PROVIDER_HINTS = {
    "local_folder": "A folder on this computer, for testing and demos.",
    "aws_s3": "An Amazon S3 bucket. Use an access key that can read and write that bucket.",
    "s3_compatible": (
        "Backblaze B2, Wasabi, Cloudflare R2, MinIO and other services that speak the S3 API. "
        "The endpoint looks like https://s3.us-west-004.backblazeb2.com."
    ),
}

FIXED_ROWS = 2  # provider type and display name; credential rows follow


class AddProviderDialog(QDialog):
    def __init__(self, provider_types, parent=None, reconnecting=None):
        """
        `reconnecting` is an existing provider record to give new credentials to. Its
        type and name are then fixed, because backups already stored there belong to it.
        """
        super().__init__(parent)
        self.reconnecting = reconnecting
        self.setWindowTitle(
            f"Reconnect {reconnecting['display_name']}" if reconnecting else "Add cloud provider"
        )
        self.setMinimumWidth(560)

        self._inputs = {}

        self.type_combo = QComboBox()
        if reconnecting:
            if reconnecting["provider_type"] in CREDENTIAL_FIELDS:
                self.type_combo.addItem(
                    reconnecting["provider_type_label"], reconnecting["provider_type"]
                )
        else:
            for provider_type in provider_types:
                if provider_type["value"] in CREDENTIAL_FIELDS:
                    self.type_combo.addItem(provider_type["label"], provider_type["value"])

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. My backup drive")
        if reconnecting:
            self.name_input.setText(reconnecting["display_name"])
            self.type_combo.setEnabled(False)
            self.name_input.setReadOnly(True)

        self.form = QFormLayout()
        self.form.setVerticalSpacing(12)
        self.form.addRow("Provider type", self.type_combo)
        self.form.addRow("Display name", self.name_input)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #555555;")
        self.hint_label.setWordWrap(True)

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
        layout.addWidget(self.hint_label)
        layout.addLayout(self.form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

        self.type_combo.currentIndexChanged.connect(self._rebuild_fields)
        self._rebuild_fields()

    def _rebuild_fields(self):
        while self.form.rowCount() > FIXED_ROWS:
            self.form.removeRow(FIXED_ROWS)
        self._inputs = {}

        provider_type = self.type_combo.currentData()
        hint = PROVIDER_HINTS.get(provider_type, "")
        if self.reconnecting:
            hint = (
                "Enter the credentials again. Use the same bucket or folder the backups "
                "were stored in, or their copies here won't be found.\n\n" + hint
            )
        self.hint_label.setText(hint)
        self.error_label.setText("")

        for key, label, kind, _required in CREDENTIAL_FIELDS.get(provider_type, []):
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
        required = {
            key
            for key, _label, _kind, is_required in CREDENTIAL_FIELDS.get(provider_type, [])
            if is_required
        }
        if provider_type is None:
            self.error_label.setText("No provider types are available.")
        elif not name:
            self.error_label.setText("Enter a display name.")
        elif not all(credentials[key] for key in required):
            self.error_label.setText("Fill in every field that isn't marked optional.")
        else:
            self.accept()
