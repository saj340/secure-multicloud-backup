from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient, ApiError


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
        self.resize(480, 360)
        self.setMinimumSize(420, 320)

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)

        field_font = QFont()
        field_font.setPointSize(11)

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

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setFont(field_font)
        self.status_label.setWordWrap(True)

        self.login_button = QPushButton("Log in")
        self.login_button.setFont(field_font)
        self.login_button.setMinimumHeight(40)
        self.login_button.clicked.connect(self.handle_login)
        self.email_input.returnPressed.connect(self.handle_login)
        self.password_input.returnPressed.connect(self.handle_login)

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

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(10)
        layout.addWidget(title_label)
        layout.addSpacing(12)
        layout.addWidget(email_label)
        layout.addWidget(self.email_input)
        layout.addWidget(password_label)
        layout.addWidget(self.password_input)
        layout.addSpacing(8)
        layout.addWidget(self.login_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.register_link)
        layout.addWidget(self.forgot_password_link)
        layout.addStretch()
        self.setLayout(layout)

    def _handle_register_click(self):
        if self.on_register_requested:
            self.on_register_requested()

    def _handle_forgot_password_click(self):
        if self.on_forgot_password_requested:
            self.on_forgot_password_requested()

    def handle_login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self.status_label.setText("Email and password are required.")
            return

        self.login_button.setEnabled(False)
        self.status_label.setText("")

        try:
            user = self.api_client.login(email, password)
        except ApiError as exc:
            self.status_label.setText(exc.message)
        else:
            self.password_input.clear()
            if self.on_login_success:
                self.on_login_success(user)
        finally:
            self.login_button.setEnabled(True)
