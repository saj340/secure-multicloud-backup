from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)


class SetDialog(QDialog):
    """Name a backup set and choose the folders and files it covers."""

    def __init__(self, backup_set=None, parent=None):
        """Pass an existing set (as the server returns it) to edit it."""
        super().__init__(parent)
        self.setWindowTitle("Edit backup set" if backup_set else "New backup set")
        self.setMinimumSize(560, 420)

        font = QFont()
        font.setPointSize(11)

        name_label = QLabel("Name")
        name_label.setFont(font)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. My documents")
        self.name_input.setFont(font)
        self.name_input.setMinimumHeight(34)

        places_label = QLabel("Folders and files in this set")
        places_label.setFont(font)
        hint = QLabel(
            "Each time you back the set up, the app looks inside these folders again, "
            "so files you add later are included."
        )
        hint.setFont(font)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555555;")

        self.sources_list = QListWidget()
        self.sources_list.setFont(font)

        self.add_folder_button = QPushButton("Add folder…")
        self.add_files_button = QPushButton("Add files…")
        self.remove_button = QPushButton("Remove selected")
        for button in (self.add_folder_button, self.add_files_button, self.remove_button):
            button.setFont(font)
        self.add_folder_button.clicked.connect(self.add_folder)
        self.add_files_button.clicked.connect(self.add_files)
        self.remove_button.clicked.connect(self.remove_selected)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_folder_button)
        buttons.addWidget(self.add_files_button)
        buttons.addStretch()
        buttons.addWidget(self.remove_button)

        self.error_label = QLabel("")
        self.error_label.setFont(font)
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setWordWrap(True)

        ok_cancel = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_cancel.accepted.connect(self._accept_if_valid)
        ok_cancel.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(name_label)
        layout.addWidget(self.name_input)
        layout.addWidget(places_label)
        layout.addWidget(hint)
        layout.addWidget(self.sources_list, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.error_label)
        layout.addWidget(ok_cancel)

        if backup_set:
            self.name_input.setText(backup_set["name"])
            self.add_paths(backup_set["sources"])

    def sources(self):
        return [self.sources_list.item(row).text() for row in range(self.sources_list.count())]

    def add_paths(self, new_paths):
        existing = set(self.sources())
        for path in new_paths:
            if path and path not in existing:
                self.sources_list.addItem(path)
                existing.add(path)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to add to the set")
        if folder:
            self.add_paths([folder])

    def add_files(self):
        files, _filter = QFileDialog.getOpenFileNames(self, "Choose files to add to the set")
        self.add_paths(files)

    def remove_selected(self):
        for item in self.sources_list.selectedItems():
            self.sources_list.takeItem(self.sources_list.row(item))

    def values(self):
        """The fields the server needs: {"name": ..., "sources": [...]}."""
        return {"name": self.name_input.text().strip(), "sources": self.sources()}

    def _accept_if_valid(self):
        values = self.values()
        if not values["name"]:
            self.error_label.setText("Give the set a name.")
        elif not values["sources"]:
            self.error_label.setText("Add at least one folder or file.")
        else:
            self.accept()
