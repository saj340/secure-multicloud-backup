from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.api_client import ApiClient, ApiError


class ForgotPasswordWindow(QWidget):
    def __init__(self, api_client: ApiClient, on_requested=None, on_login_requested=None):
        super().__init__()
        self.api_client = api_client
        self.on_requested = on_requested
        self.on_login_requested = on_login_requested

        self.setWindowTitle("Secure Multi-Cloud Backup — Forgot password")
        self.resize(480, 320)
        self.setMinimumSize(420, 300)

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)

        field_font = QFont()
        field_font.setPointSize(11)

        title_label = QLabel("Reset your password")
        title_label.setFont(title_font)

        info_label = QLabel(
            "Enter your account email. If it's registered, a reset code will be sent to it."
        )
        info_label.setFont(field_font)
        info_label.setWordWrap(True)

        email_label = QLabel("Email")
        email_label.setFont(field_font)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        self.email_input.setFont(field_font)
        self.email_input.setMinimumHeight(36)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setFont(field_font)
        self.status_label.setWordWrap(True)

        self.send_button = QPushButton("Send reset code")
        self.send_button.setFont(field_font)
        self.send_button.setMinimumHeight(40)
        self.send_button.clicked.connect(self.handle_send)

        self.login_link = QPushButton("Back to login")
        self.login_link.setFont(field_font)
        self.login_link.setFlat(True)
        self.login_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_link.clicked.connect(self._handle_login_click)

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(10)
        layout.addWidget(title_label)
        layout.addWidget(info_label)
        layout.addSpacing(8)
        layout.addWidget(email_label)
        layout.addWidget(self.email_input)
        layout.addSpacing(8)
        layout.addWidget(self.send_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.login_link)
        layout.addStretch()
        self.setLayout(layout)

    def handle_send(self):
        email = self.email_input.text().strip()
        if not email:
            self.status_label.setText("Email is required.")
            return

        self.send_button.setEnabled(False)
        self.status_label.setText("")

        try:
            self.api_client.request_password_reset(email)
        except ApiError as exc:
            self.status_label.setText(exc.message)
        else:
            QMessageBox.information(
                self,
                "Check your email",
                "If that email is registered, a reset code has been sent.\n\n"
                "(Dev mode: check the Django server's console output for the code.)",
            )
            if self.on_requested:
                self.on_requested()
        finally:
            self.send_button.setEnabled(True)

    def _handle_login_click(self):
        if self.on_login_requested:
            self.on_login_requested()
