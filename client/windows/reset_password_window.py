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


class ResetPasswordWindow(QWidget):
    def __init__(self, api_client: ApiClient, on_reset=None, on_login_requested=None):
        super().__init__()
        self.api_client = api_client
        self.on_reset = on_reset
        self.on_login_requested = on_login_requested

        self.setWindowTitle("Secure Multi-Cloud Backup — Reset password")
        self.resize(480, 420)
        self.setMinimumSize(420, 400)

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)

        field_font = QFont()
        field_font.setPointSize(11)

        title_label = QLabel("Enter your reset code")
        title_label.setFont(title_font)

        code_label = QLabel("Reset code")
        code_label.setFont(field_font)
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("Paste the code from your email")
        self.code_input.setFont(field_font)
        self.code_input.setMinimumHeight(36)

        password_label = QLabel("New password")
        password_label.setFont(field_font)
        self.password1_input = QLineEdit()
        self.password1_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password1_input.setFont(field_font)
        self.password1_input.setMinimumHeight(36)

        confirm_label = QLabel("Confirm new password")
        confirm_label.setFont(field_font)
        self.password2_input = QLineEdit()
        self.password2_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password2_input.setFont(field_font)
        self.password2_input.setMinimumHeight(36)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setFont(field_font)
        self.status_label.setWordWrap(True)

        self.reset_button = QPushButton("Reset password")
        self.reset_button.setFont(field_font)
        self.reset_button.setMinimumHeight(40)
        self.reset_button.clicked.connect(self.handle_reset)

        self.login_link = QPushButton("Back to login")
        self.login_link.setFont(field_font)
        self.login_link.setFlat(True)
        self.login_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_link.clicked.connect(self._handle_login_click)

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(10)
        layout.addWidget(title_label)
        layout.addSpacing(8)
        layout.addWidget(code_label)
        layout.addWidget(self.code_input)
        layout.addWidget(password_label)
        layout.addWidget(self.password1_input)
        layout.addWidget(confirm_label)
        layout.addWidget(self.password2_input)
        layout.addSpacing(8)
        layout.addWidget(self.reset_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.login_link)
        layout.addStretch()
        self.setLayout(layout)

    def handle_reset(self):
        code = self.code_input.text().strip()
        password1 = self.password1_input.text()
        password2 = self.password2_input.text()

        if not code or not password1 or not password2:
            self.status_label.setText("All fields are required.")
            return

        self.reset_button.setEnabled(False)
        self.status_label.setText("")

        try:
            self.api_client.confirm_password_reset(code, password1, password2)
        except ApiError as exc:
            self.status_label.setText(exc.message)
        else:
            QMessageBox.information(
                self,
                "Password reset",
                "Your password has been reset. You can now log in.",
            )
            if self.on_reset:
                self.on_reset()
        finally:
            self.reset_button.setEnabled(True)

    def _handle_login_click(self):
        if self.on_login_requested:
            self.on_login_requested()
