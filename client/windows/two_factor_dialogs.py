"""
The screens for two-factor login: turning it on (password, then scan a QR code and prove the
app works, then save the backup codes), and confirming a sensitive change (turning it off or
renewing the backup codes) with the password plus a current code.
"""

from pathlib import Path

import segno
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient
from client.workers import run_in_background

from .progress_panel import ERROR_STYLE

QUIET_ZONE = 4  # blank modules around a QR code that scanners need
MUTED_STYLE = "color: #555555;"


def qr_matrix(text: str) -> list[list[bool]]:
    """The QR code for `text` as rows of dark (True) / light (False) squares."""
    return [[bool(cell) for cell in row] for row in segno.make(text, error="m").matrix]


class QrWidget(QWidget):
    """Draws a QR code. Always black on white with a blank border, so any app can scan it."""

    def __init__(self, size=220):
        super().__init__()
        self._matrix = []
        self.setFixedSize(size, size)

    def set_text(self, text):
        self._matrix = qr_matrix(text)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        if not self._matrix:
            return
        modules = len(self._matrix) + 2 * QUIET_ZONE
        cell = min(self.width(), self.height()) / modules
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("black"))
        for y, row in enumerate(self._matrix):
            for x, dark in enumerate(row):
                if dark:
                    painter.drawRect(
                        int((x + QUIET_ZONE) * cell), int((y + QUIET_ZONE) * cell),
                        int(cell) + 1, int(cell) + 1,
                    )


def _font(size=11, mono=False):
    font = QFont("Consolas") if mono else QFont()
    font.setPointSize(size)
    return font


def _label(text, muted=False):
    label = QLabel(text)
    label.setFont(_font())
    label.setWordWrap(True)
    if muted:
        label.setStyleSheet(MUTED_STYLE)
    return label


def _line_edit(placeholder="", password=False):
    edit = QLineEdit()
    edit.setFont(_font())
    edit.setMinimumHeight(34)
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class BackupCodesView(QWidget):
    """The one-time backup codes, with copy and save buttons and a box to tick once they are safe."""

    def __init__(self):
        super().__init__()
        self.codes_box = QPlainTextEdit()
        self.codes_box.setReadOnly(True)
        self.codes_box.setFont(_font(13, mono=True))
        self.codes_box.setFixedHeight(190)
        self.copy_button = QPushButton("Copy")
        self.save_button = QPushButton("Save to a file…")
        for button in (self.copy_button, self.save_button):
            button.setFont(_font())
        self.copy_button.clicked.connect(self.copy)
        self.save_button.clicked.connect(self.save)
        self.saved_checkbox = QCheckBox("I have saved these codes somewhere safe")
        self.saved_checkbox.setFont(_font())

        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.save_button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_label(
            "Each code works once and replaces your authenticator app if you lose your phone. "
            "They are shown only now, so save them before you continue: keep them somewhere "
            "other than this computer, such as a password manager or a printout.", muted=True
        ))
        layout.addWidget(self.codes_box)
        layout.addLayout(buttons)
        layout.addWidget(self.saved_checkbox)

    def set_codes(self, codes):
        self.codes_box.setPlainText("\n".join(codes))
        self.saved_checkbox.setChecked(False)

    def codes_text(self):
        return self.codes_box.toPlainText()

    def copy(self):
        QApplication.clipboard().setText(self.codes_text())

    def save(self):
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save backup codes", "backup-codes.txt", "Text files (*.txt)"
        )
        if path:
            Path(path).write_text(
                "Secure Multi-Cloud Backup: two-factor backup codes\n"
                "Each code works once.\n\n" + self.codes_text() + "\n",
                encoding="utf-8",
            )


class TurnOnDialog(QDialog):
    """Three steps: confirm the password, scan the QR code and enter a code, save the backup codes."""

    PASSWORD_PAGE, SCAN_PAGE, CODES_PAGE = range(3)

    def __init__(self, api_client: ApiClient, parent=None):
        super().__init__(parent)
        self.api_client = api_client
        self.enabled = False  # true once the server has switched two-factor on
        self._busy = False
        self.setWindowTitle("Turn on two-factor login")
        self.setMinimumSize(520, 560)

        # Step 1: the password
        self.password_input = _line_edit("Your password", password=True)
        self.password_input.returnPressed.connect(self.start_setup)
        self.next_button = QPushButton("Next")
        self.next_button.setFont(_font())
        self.next_button.clicked.connect(self.start_setup)
        page1 = QWidget()
        layout1 = QVBoxLayout(page1)
        layout1.addWidget(_label(
            "Two-factor login asks for a code from an app on your phone every time you log in, "
            "so a stolen or guessed password is not enough. Confirm your password to begin."
        ))
        layout1.addWidget(self.password_input)
        layout1.addWidget(self.next_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout1.addStretch()

        # Step 2: scan, then prove it works
        self.qr = QrWidget()
        self.key_label = QLabel("")
        self.key_label.setFont(_font(12, mono=True))
        self.key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.key_label.setWordWrap(True)
        self.code_input = _line_edit("6-digit code from the app")
        self.code_input.setMaxLength(12)
        self.code_input.returnPressed.connect(self.confirm_code)
        self.confirm_button = QPushButton("Turn on")
        self.confirm_button.setFont(_font())
        self.confirm_button.clicked.connect(self.confirm_code)
        page2 = QWidget()
        layout2 = QVBoxLayout(page2)
        layout2.addWidget(_label(
            "1. Open an authenticator app (Google Authenticator, Microsoft Authenticator, Authy…) "
            "and scan this code."
        ))
        layout2.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout2.addWidget(_label("Can't scan it? Type this key into the app instead:", muted=True))
        layout2.addWidget(self.key_label)
        layout2.addWidget(_label("2. Enter the 6-digit code the app now shows, to check that it works."))
        layout2.addWidget(self.code_input)
        layout2.addWidget(self.confirm_button, alignment=Qt.AlignmentFlag.AlignRight)

        # Step 3: the backup codes
        self.codes_view = BackupCodesView()
        self.done_button = QPushButton("Done")
        self.done_button.setFont(_font())
        self.done_button.setEnabled(False)
        self.done_button.clicked.connect(self.accept)
        self.codes_view.saved_checkbox.toggled.connect(self.done_button.setEnabled)
        page3 = QWidget()
        layout3 = QVBoxLayout(page3)
        layout3.addWidget(_label("Two-factor login is on. One last step: save your backup codes."))
        layout3.addWidget(self.codes_view)
        layout3.addWidget(self.done_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout3.addStretch()

        self.pages = QStackedWidget()
        for page in (page1, page2, page3):
            self.pages.addWidget(page)
        self.message = QLabel("")
        self.message.setFont(_font())
        self.message.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.message)

    # ---------------------------------------------------------------- steps

    def _show(self, text, style=ERROR_STYLE):
        self.message.setStyleSheet(style)
        self.message.setText(text)

    def _set_busy(self, busy):
        self._busy = busy
        self.next_button.setEnabled(not busy)
        self.confirm_button.setEnabled(not busy)

    def start_setup(self):
        if self._busy:
            return
        password = self.password_input.text()
        if not password:
            return self._show("Enter your password.")
        self._set_busy(True)
        self._show("Working…", MUTED_STYLE)
        run_in_background(
            lambda: self.api_client.two_factor_setup(password), self._setup_done, self._failed
        )

    def _setup_done(self, data):
        self._set_busy(False)
        self.password_input.clear()
        self.qr.set_text(data["otpauth_uri"])
        secret = data["secret"]
        self.key_label.setText(" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))
        self.message.setText("")
        self.pages.setCurrentIndex(self.SCAN_PAGE)
        self.code_input.setFocus()

    def confirm_code(self):
        if self._busy:
            return
        code = self.code_input.text().strip()
        if not code:
            return self._show("Enter the code from the app.")
        self._set_busy(True)
        self._show("Checking…", MUTED_STYLE)
        run_in_background(lambda: self.api_client.two_factor_enable(code), self._enabled, self._failed)

    def _enabled(self, codes):
        self._set_busy(False)
        self.enabled = True
        self.codes_view.set_codes(codes)
        self.message.setText("")
        self.pages.setCurrentIndex(self.CODES_PAGE)

    def _failed(self, message):
        self._set_busy(False)
        self.code_input.clear()
        self._show(message)

    def reject(self):
        if self._busy:
            return
        if self.enabled and not self.codes_view.saved_checkbox.isChecked():
            # The codes can't be shown again, so don't let them be dismissed unsaved.
            return self._show("Save your backup codes first, then tick the box.")
        super().reject()


class BackupCodesDialog(QDialog):
    """Shows newly created backup codes; can't be dismissed until they are marked as saved."""

    def __init__(self, codes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Your new backup codes")
        self.setMinimumSize(480, 400)
        self.view = BackupCodesView()
        self.view.set_codes(codes)
        self.done_button = QPushButton("Done")
        self.done_button.setFont(_font())
        self.done_button.setEnabled(False)
        self.done_button.clicked.connect(self.accept)
        self.view.saved_checkbox.toggled.connect(self.done_button.setEnabled)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(_label("Your old backup codes no longer work. Use these instead."))
        layout.addWidget(self.view)
        layout.addWidget(self.done_button, alignment=Qt.AlignmentFlag.AlignRight)

    def reject(self):
        if self.view.saved_checkbox.isChecked():
            super().reject()


class ConfirmIdentityDialog(QDialog):
    """Asks for the password and a current code, before turning two-factor off or renewing codes."""

    def __init__(self, title, explanation, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.password_input = _line_edit("Your password", password=True)
        self.code_input = _line_edit("Code from the app, or a backup code")
        self.code_input.setMaxLength(64)
        self.error_label = QLabel("")
        self.error_label.setFont(_font())
        self.error_label.setStyleSheet(ERROR_STYLE)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_filled)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(_label(explanation))
        layout.addWidget(self.password_input)
        layout.addWidget(self.code_input)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def values(self):
        """(password, code) as typed."""
        return self.password_input.text(), self.code_input.text().strip()

    def _accept_if_filled(self):
        password, code = self.values()
        if not password or not code:
            self.error_label.setText("Enter both your password and a code.")
        else:
            self.accept()
