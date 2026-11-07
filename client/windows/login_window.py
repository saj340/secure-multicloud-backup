from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient, ApiError, TwoFactorRequired

ERROR_STYLE = "color: #b00020;"
NOTICE_STYLE = "color: #b26a00;"


class LoginWindow(QWidget):
    def __init__(
        self,
        api_client: ApiClient,
        on_register_requested=None,
        on_forgot_password_requested=None,
        on_login_success=None,
    ):
        super().__init__()
        self.api_client = api_client
        self.on_register_requested = on_register_requested
        self.on_forgot_password_requested = on_forgot_password_requested
        self.on_login_success = on_login_success
        self.setWindowTitle("Secure Multi-Cloud Backup — Log in")
        self.resize(480, 400)
        self.setMinimumSize(420, 360)

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)

        field_font = QFont()
        field_font.setPointSize(11)

        small_font = QFont()
        small_font.setPointSize(9)

        title_label = QLabel("Log in")
        title_label.setFont(title_font)

        email_label = QLabel("Email")
        email_label.setFont(field_font)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        self.email_input.setFont(field_font)
        self.email_input.setMinimumHeight(36)

        password_label = QLabel("Password")
        password_label.setFont(field_font)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setFont(field_font)
        self.password_input.setMinimumHeight(36)

        # Shown only when the account has two-factor login: a code from the authenticator app.
        self.code_label = QLabel("Verification code")
        self.code_label.setFont(field_font)
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("6-digit code, or a backup code")
        self.code_input.setFont(field_font)
        self.code_input.setMinimumHeight(36)
        self.code_input.setMaxLength(64)
        self.other_account_link = QPushButton("Use a different account")
        self.other_account_link.setFont(small_font)
        self.other_account_link.setFlat(True)
        self.other_account_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.other_account_link.clicked.connect(self._reset_two_factor)
        self._awaiting_code = False
        self._set_code_step_visible(False)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(ERROR_STYLE)
        self.status_label.setFont(field_font)
        self.status_label.setWordWrap(True)

        self.login_button = QPushButton("Log in")
        self.login_button.setFont(field_font)
        self.login_button.setMinimumHeight(40)
        self.login_button.clicked.connect(self.handle_login)
        self.email_input.returnPressed.connect(self.handle_login)
        self.password_input.returnPressed.connect(self.handle_login)
        self.code_input.returnPressed.connect(self.handle_login)

        self.register_link = QPushButton("Need an account? Register")
        self.register_link.setFont(field_font)
        self.register_link.setFlat(True)
        self.register_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.register_link.clicked.connect(self._handle_register_click)

        self.forgot_password_link = QPushButton("Forgot password?")
        self.forgot_password_link.setFont(field_font)
        self.forgot_password_link.setFlat(True)
        self.forgot_password_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_password_link.clicked.connect(self._handle_forgot_password_click)

        server = urlparse(api_client.base_url)
        encrypted = server.scheme == "https"
        self.server_label = QLabel(
            f"Encrypted (TLS) connection to {server.netloc}"
            if encrypted
            else f"Not encrypted: local development server at {server.netloc}"
        )
        self.server_label.setFont(small_font)
        self.server_label.setStyleSheet("color: #1b7f3b;" if encrypted else NOTICE_STYLE)

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 20)
        layout.setSpacing(10)
        layout.addWidget(title_label)
        layout.addSpacing(12)
        layout.addWidget(email_label)
        layout.addWidget(self.email_input)
        layout.addWidget(password_label)
        layout.addWidget(self.password_input)
        layout.addWidget(self.code_label)
        layout.addWidget(self.code_input)
        layout.addSpacing(8)
        layout.addWidget(self.login_button)
        layout.addWidget(self.other_account_link)
        layout.addWidget(self.status_label)
        layout.addWidget(self.register_link)
        layout.addWidget(self.forgot_password_link)
        layout.addStretch()
        layout.addWidget(self.server_label)
        self.setLayout(layout)

    def show_notice(self, message):
        """A message that isn't an error, e.g. that the session expired."""
        self.status_label.setStyleSheet(NOTICE_STYLE)
        self.status_label.setText(message)

    def _handle_register_click(self):
        if self.on_register_requested:
            self.on_register_requested()

    def _handle_forgot_password_click(self):
        if self.on_forgot_password_requested:
            self.on_forgot_password_requested()

    def _set_code_step_visible(self, visible):
        for widget in (self.code_label, self.code_input, self.other_account_link):
            widget.setVisible(visible)

    def _ask_for_code(self):
        """The password was right; the account also needs its second factor."""
        self._awaiting_code = True
        self._set_code_step_visible(True)
        # The password has been accepted, so lock it in: changing it now would just fail.
        self.email_input.setReadOnly(True)
        self.password_input.setReadOnly(True)
        self.login_button.setText("Verify and log in")
        self.status_label.setStyleSheet(NOTICE_STYLE)
        self.status_label.setText("Enter the code from your authenticator app.")
        self.code_input.setFocus()

    def _reset_two_factor(self):
        self._awaiting_code = False
        self._set_code_step_visible(False)
        self.code_input.clear()
        self.email_input.setReadOnly(False)
        self.password_input.setReadOnly(False)
        self.login_button.setText("Log in")
        self.status_label.setText("")

    def handle_login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()
        code = self.code_input.text().strip() if self._awaiting_code else None
        self.status_label.setStyleSheet(ERROR_STYLE)

        if not email or not password:
            self.status_label.setText("Email and password are required.")
            return
        if self._awaiting_code and not code:
            self.status_label.setText("Enter the code from your authenticator app.")
            return

        self.login_button.setEnabled(False)
        self.status_label.setText("")

        try:
            user = self.api_client.login(email, password, code)
        except TwoFactorRequired:
            self._ask_for_code()
        except ApiError as exc:
            self.status_label.setText(exc.message)
            self.code_input.clear()  # a wrong code is retyped, not edited
        else:
            self.password_input.clear()
            self._reset_two_factor()
            if self.on_login_success:
                self.on_login_success(user)
        finally:
            self.login_button.setEnabled(True)
