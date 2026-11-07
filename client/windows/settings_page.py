from urllib.parse import urlparse

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient
from client.workers import run_in_background

from .progress_panel import ERROR_STYLE, OK_STYLE
from .two_factor_dialogs import BackupCodesDialog, ConfirmIdentityDialog, TurnOnDialog


class SettingsPage(QWidget):
    def __init__(self, api_client: ApiClient, on_profile_changed=None):
        super().__init__()
        self.api_client = api_client
        self.on_profile_changed = on_profile_changed
        self._busy = False

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Settings")
        title.setFont(title_font)

        # -------------------------------------------------------- profile
        self.email_label = QLabel("")
        self.email_label.setFont(body_font)
        self.name_input = QLineEdit()
        self.name_input.setFont(body_font)
        self.name_input.setMaxLength(150)
        self.name_input.setMinimumHeight(34)
        self.save_button = QPushButton("Save name")
        self.save_button.setFont(body_font)
        self.save_button.clicked.connect(self.save_profile)
        self.profile_message = self._message_label(body_font)

        profile_form = QFormLayout()
        profile_form.setVerticalSpacing(10)
        profile_form.addRow("Email", self.email_label)
        profile_form.addRow("Full name", self.name_input)
        profile_layout = QVBoxLayout()
        profile_layout.addLayout(profile_form)
        profile_layout.addWidget(self.save_button)
        profile_layout.addWidget(self.profile_message)
        self.profile_box = QGroupBox("Profile")
        self.profile_box.setFont(body_font)
        self.profile_box.setLayout(profile_layout)

        # ------------------------------------------------------- password
        self.current_input = self._password_input(body_font)
        self.new_input = self._password_input(body_font)
        self.confirm_input = self._password_input(body_font)
        self.change_button = QPushButton("Change password")
        self.change_button.setFont(body_font)
        self.change_button.clicked.connect(self.change_password)
        self.password_message = self._message_label(body_font)

        password_form = QFormLayout()
        password_form.setVerticalSpacing(10)
        password_form.addRow("Current password", self.current_input)
        password_form.addRow("New password", self.new_input)
        password_form.addRow("Confirm new password", self.confirm_input)
        password_layout = QVBoxLayout()
        password_layout.addLayout(password_form)
        password_layout.addWidget(self.change_button)
        password_layout.addWidget(self.password_message)
        self.password_box = QGroupBox("Change password")
        self.password_box.setFont(body_font)
        self.password_box.setLayout(password_layout)

        # ----------------------------------------------------- two-factor
        self.two_factor_status = QLabel("")
        self.two_factor_status.setFont(body_font)
        self.two_factor_status.setWordWrap(True)
        self.turn_on_button = QPushButton("Turn on…")
        self.turn_off_button = QPushButton("Turn off…")
        self.new_codes_button = QPushButton("New backup codes…")
        for button in (self.turn_on_button, self.turn_off_button, self.new_codes_button):
            button.setFont(body_font)
        self.turn_on_button.clicked.connect(self.turn_on_two_factor)
        self.turn_off_button.clicked.connect(self.turn_off_two_factor)
        self.new_codes_button.clicked.connect(self.renew_backup_codes)
        self.two_factor_message = self._message_label(body_font)
        two_factor_buttons = QHBoxLayout()
        two_factor_buttons.addWidget(self.turn_on_button)
        two_factor_buttons.addWidget(self.turn_off_button)
        two_factor_buttons.addWidget(self.new_codes_button)
        two_factor_buttons.addStretch()
        two_factor_layout = QVBoxLayout()
        two_factor_layout.addWidget(self.two_factor_status)
        two_factor_layout.addLayout(two_factor_buttons)
        two_factor_layout.addWidget(self.two_factor_message)
        self.two_factor_box = QGroupBox("Two-factor login")
        self.two_factor_box.setFont(body_font)
        self.two_factor_box.setLayout(two_factor_layout)
        self._two_factor_on = False

        # ----------------------------------------------------- connection
        server = urlparse(api_client.base_url)
        encrypted = server.scheme == "https"
        self.server_label = QLabel(
            f"Encrypted (TLS) connection to {server.netloc}"
            if encrypted
            else f"Not encrypted: local development server at {server.netloc}"
        )
        self.server_label.setFont(body_font)
        self.server_label.setStyleSheet(OK_STYLE if encrypted else "color: #b26a00;")
        note = QLabel(
            "Your files are encrypted on this device before anything is uploaded. "
            "Only one device can be signed in to an account at a time, and a "
            "session ends after a while, so you may be asked to log in again."
        )
        note.setFont(body_font)
        note.setWordWrap(True)
        note.setStyleSheet("color: #555555;")
        connection_layout = QVBoxLayout()
        connection_layout.addWidget(self.server_label)
        connection_layout.addWidget(note)
        self.connection_box = QGroupBox("Connection and sessions")
        self.connection_box.setFont(body_font)
        self.connection_box.setLayout(connection_layout)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(self.profile_box)
        layout.addWidget(self.password_box)
        layout.addWidget(self.two_factor_box)
        layout.addWidget(self.connection_box)
        layout.addStretch()

    @staticmethod
    def _password_input(font):
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setFont(font)
        edit.setMinimumHeight(34)
        return edit

    @staticmethod
    def _message_label(font):
        label = QLabel("")
        label.setFont(font)
        label.setWordWrap(True)
        return label

    # ------------------------------------------------------------- profile

    def set_user(self, user):
        self.email_label.setText(user["email"])
        self.name_input.setText(user.get("full_name") or "")
        self._two_factor_on = bool(user.get("two_factor_enabled"))
        if self._two_factor_on:
            left = user.get("backup_codes_left", 0)
            self.two_factor_status.setStyleSheet(OK_STYLE)
            self.two_factor_status.setText(
                f"On. You are asked for a code from your authenticator app when you log in. "
                f"Backup codes left: {left}."
            )
        else:
            self.two_factor_status.setStyleSheet("color: #555555;")
            self.two_factor_status.setText(
                "Off. Turning it on means a stolen or guessed password is not enough to log in."
            )
        self._update_buttons()

    def refresh(self):
        run_in_background(self.api_client.me, self.set_user, self._load_failed)

    def _load_failed(self, message):
        self._show(self.profile_message, message, ERROR_STYLE)

    def save_profile(self):
        name = self.name_input.text().strip()
        self._start(
            self.profile_message,
            "Saving…",
            lambda: self.api_client.update_profile(name),
            self._profile_saved,
        )

    def _profile_saved(self, user):
        self._busy = False
        self._update_buttons()
        self.set_user(user)
        self._show(self.profile_message, "Saved.", OK_STYLE)
        if self.on_profile_changed:
            self.on_profile_changed(user)

    # ------------------------------------------------------------ password

    def change_password(self):
        current = self.current_input.text()
        new = self.new_input.text()
        confirm = self.confirm_input.text()
        if not (current and new and confirm):
            return self._show(self.password_message, "Fill in all three fields.", ERROR_STYLE)
        if new != confirm:
            return self._show(self.password_message, "The new passwords don't match.", ERROR_STYLE)

        self._start(
            self.password_message,
            "Changing password…",
            lambda: self.api_client.change_password(current, new, confirm),
            self._password_changed,
        )

    def _password_changed(self, _result):
        self._busy = False
        self._update_buttons()
        for edit in (self.current_input, self.new_input, self.confirm_input):
            edit.clear()
        self._show(
            self.password_message,
            "Password changed. Any other devices have been signed out.",
            OK_STYLE,
        )

    # --------------------------------------------------------- two-factor

    def turn_on_two_factor(self):
        dialog = TurnOnDialog(self.api_client, self)
        dialog.exec()
        if dialog.enabled:
            self._show(self.two_factor_message, "Two-factor login is on.", OK_STYLE)
        self.refresh()

    def turn_off_two_factor(self):
        values = self._ask_identity(
            "Turn off two-factor login",
            "Turning it off means your password alone is enough to log in again. "
            "Confirm with your password and a current code (or a backup code).",
        )
        if values:
            self._start(
                self.two_factor_message,
                "Turning off…",
                lambda: self.api_client.two_factor_disable(*values),
                self._two_factor_changed("Two-factor login is off."),
            )

    def renew_backup_codes(self):
        values = self._ask_identity(
            "New backup codes",
            "Creating new backup codes makes the old ones stop working. "
            "Confirm with your password and a current code (or a backup code).",
        )
        if values:
            self._start(
                self.two_factor_message,
                "Creating new codes…",
                lambda: self.api_client.renew_backup_codes(*values),
                self._backup_codes_renewed,
            )

    def _ask_identity(self, title, explanation):
        dialog = ConfirmIdentityDialog(title, explanation, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.values()
        return None

    def _two_factor_changed(self, message):
        def done(_result):
            self._busy = False
            self._update_buttons()
            self._show(self.two_factor_message, message, OK_STYLE)
            self.refresh()
        return done

    def _backup_codes_renewed(self, codes):
        self._busy = False
        self._update_buttons()
        self._show(self.two_factor_message, "New backup codes created.", OK_STYLE)
        BackupCodesDialog(codes, self).exec()
        self.refresh()

    # ------------------------------------------------------------- helpers

    def _start(self, message_label, message, fn, on_success):
        self._busy = True
        self._update_buttons()
        self._show(message_label, message, "color: #555555;")
        run_in_background(fn, on_success, lambda text: self._failed(message_label, text))

    def _failed(self, message_label, message):
        self._busy = False
        self._update_buttons()
        self._show(message_label, message, ERROR_STYLE)

    @staticmethod
    def _show(label, message, style):
        label.setStyleSheet(style)
        label.setText(message)

    def _update_buttons(self):
        self.save_button.setEnabled(not self._busy)
        self.change_button.setEnabled(not self._busy)
        self.turn_on_button.setEnabled(not self._busy and not self._two_factor_on)
        self.turn_off_button.setEnabled(not self._busy and self._two_factor_on)
        self.new_codes_button.setEnabled(not self._busy and self._two_factor_on)
