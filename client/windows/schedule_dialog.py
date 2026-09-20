from datetime import datetime

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def local_utc_offset_minutes() -> int:
    """This computer's current offset from UTC, so the server can read a time as local."""
    return int(datetime.now().astimezone().utcoffset().total_seconds() // 60)


class ScheduleDialog(QDialog):
    def __init__(self, schedule=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit schedule" if schedule else "Add schedule")
        self.setMinimumWidth(560)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Documents")

        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText("A folder or file to back up")
        folder_button = QPushButton("Folder…")
        file_button = QPushButton("File…")
        folder_button.clicked.connect(self._browse_folder)
        file_button.clicked.connect(self._browse_file)
        source_row = QWidget()
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self.source_input)
        source_layout.addWidget(folder_button)
        source_layout.addWidget(file_button)

        self.frequency_combo = QComboBox()
        self.frequency_combo.addItem("Daily", "daily")
        self.frequency_combo.addItem("Weekly", "weekly")
        self.frequency_combo.currentIndexChanged.connect(self._frequency_changed)

        self.weekday_combo = QComboBox()
        self.weekday_combo.addItems(WEEKDAYS)

        self.time_input = QTimeEdit(QTime(2, 0))
        self.time_input.setDisplayFormat("HH:mm")

        self.active_check = QCheckBox("Active")
        self.active_check.setChecked(True)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.addRow("Name", self.name_input)
        form.addRow("Back up", source_row)
        form.addRow("How often", self.frequency_combo)
        form.addRow("Day", self.weekday_combo)
        form.addRow("At", self.time_input)
        form.addRow("", self.active_check)

        hint = QLabel(
            "Scheduled backups run while this app is open and you are logged in. If the app "
            "was closed at the scheduled time, the backup runs when you next open it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555555;")

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
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

        if schedule:
            self._fill(schedule)
        self._frequency_changed()

    def _fill(self, schedule):
        self.name_input.setText(schedule["name"])
        self.source_input.setText(schedule["source_path"])
        self.frequency_combo.setCurrentIndex(self.frequency_combo.findData(schedule["frequency"]))
        if schedule["weekday"] is not None:
            self.weekday_combo.setCurrentIndex(schedule["weekday"])
        self.time_input.setTime(QTime.fromString(schedule["run_at"], "HH:mm"))
        self.active_check.setChecked(schedule["is_active"])

    def _frequency_changed(self):
        self.weekday_combo.setEnabled(self.frequency_combo.currentData() == "weekly")

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to back up")
        if folder:
            self.source_input.setText(folder)

    def _browse_file(self):
        file, _filter = QFileDialog.getOpenFileName(self, "Choose a file to back up")
        if file:
            self.source_input.setText(file)

    def values(self):
        """The schedule as the API wants it."""
        frequency = self.frequency_combo.currentData()
        return {
            "name": self.name_input.text().strip(),
            "source_path": self.source_input.text().strip(),
            "frequency": frequency,
            "run_at": self.time_input.time().toString("HH:mm"),
            "weekday": self.weekday_combo.currentIndex() if frequency == "weekly" else None,
            "is_active": self.active_check.isChecked(),
            "utc_offset_minutes": local_utc_offset_minutes(),
        }

    def _accept_if_valid(self):
        values = self.values()
        if not values["name"]:
            self.error_label.setText("Enter a name for this schedule.")
        elif not values["source_path"]:
            self.error_label.setText("Choose a folder or file to back up.")
        else:
            self.accept()
