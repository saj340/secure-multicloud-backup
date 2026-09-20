from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

OK_STYLE = "color: #1b7f3b;"
ERROR_STYLE = "color: #b00020;"
WARN_STYLE = "color: #b26a00;"

STAGE_TEXT = {
    "preparing": "Preparing…",
    "protecting": "Protecting — encrypting on this device",
    "uploading": "Uploading the encrypted data",
    "downloading": "Downloading the encrypted data",
    "decrypting": "Decrypting on this device",
    "completed": "Completed",
}


def files_text(count):
    return f"{count} file" if count == 1 else f"{count} files"


class ProgressPanel(QWidget):
    """What a backup or restore shows while it runs and once it has finished."""

    def __init__(self):
        super().__init__()
        body_font = QFont()
        body_font.setPointSize(11)

        self.stage_label = QLabel("")
        self.stage_label.setFont(body_font)
        self.file_label = QLabel("")
        self.file_label.setFont(body_font)
        self.file_label.setStyleSheet("color: #555555;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)

        self.result_label = QLabel("")
        self.result_label.setFont(body_font)
        self.result_label.setWordWrap(True)

        self.failures_box = QPlainTextEdit()
        self.failures_box.setReadOnly(True)
        self.failures_box.setMaximumHeight(120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in (
            self.stage_label,
            self.file_label,
            self.progress_bar,
            self.result_label,
            self.failures_box,
        ):
            layout.addWidget(widget)

        self._set_progress_visible(False)
        self.failures_box.hide()

    def start(self):
        self.show_message("", OK_STYLE)
        self.failures_box.hide()
        self.stage_label.setText(STAGE_TEXT["preparing"])
        self.file_label.setText("")
        self.progress_bar.setRange(0, 0)  # busy indicator until a total is known
        self._set_progress_visible(True)

    def progress(self, stage, detail, done=None, total=None):
        if stage in STAGE_TEXT:
            self.stage_label.setText(STAGE_TEXT[stage])
        if total:
            self.progress_bar.setRange(0, total)
            if stage in ("processed", "completed"):
                self.progress_bar.setValue(done)
        if stage in ("protecting", "uploading") and total:
            self.file_label.setText(f"{Path(detail).name}  ({done + 1} of {total})")

    def show_message(self, message, style):
        self.result_label.setStyleSheet(style)
        self.result_label.setText(message)

    def show_backup_result(self, result) -> bool:
        """Show how a backup run ended. Returns True if every file was backed up."""
        self.file_label.setText("")
        total = len(result.succeeded) + len(result.failed)

        if result.status == "completed":
            self.show_message(
                f"Backup completed: {files_text(total)} encrypted and stored across your providers.",
                OK_STYLE,
            )
            return True

        if result.failed:
            message = (
                f"Backup finished with problems: {len(result.failed)} of {files_text(total)} "
                "could not be backed up."
            )
            self.failures_box.setPlainText(
                "\n".join(f"{path}\n    {reason}" for path, reason in result.failed.items())
            )
            self.failures_box.show()
        else:
            message = "The server reported this backup as failed."
        self.stage_label.setText("Finished with problems")
        self.show_message(message, ERROR_STYLE)
        return False

    def finish_ok(self, message):
        self.stage_label.setText(STAGE_TEXT["completed"])
        self.file_label.setText("")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.show_message(message, OK_STYLE)

    def show_failure(self, message):
        self._set_progress_visible(False)
        self.show_message(message, ERROR_STYLE)

    def _set_progress_visible(self, visible):
        for widget in (self.stage_label, self.file_label, self.progress_bar):
            widget.setVisible(visible)
