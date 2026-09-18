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


class RegisterWindow(QWidget):
    def __init__(self, api_client: ApiClient, on_registered=None, on_login_requested=None):
        super().__init__()
        self.api_client = api_client
        self.on_registered = on_registered
        self.on_login_requested = on_login_requested

        self.setWindowTitle("Secure Multi-Cloud Backup — Register")
        self.resize(480, 460)
        self.setMinimumSize(420, 420)

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)

        field_font = QFont()
        field_font.setPointSize(11)

        title_label = QLabel("Create an account")
        title_label.setFont(title_font)

        email_label = QLabel("Email")
        email_label.setFont(field_font)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        self.email_input.setFont(field_font)
        self.email_input.setMinimumHeight(36)

        full_name_label = QLabel("Full name")
        full_name_label.setFont(field_font)
        self.full_name_input = QLineEdit()
        self.full_name_input.setPlaceholderText("Optional")
        self.full_name_input.setFont(field_font)
        self.full_name_input.setMinimumHeight(36)

        password_label = QLabel("Password")
        password_label.setFont(field_font)
        self.password1_input = QLineEdit()
        self.password1_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password1_input.setFont(field_font)
        self.password1_input.setMinimumHeight(36)

        confirm_label = QLabel("Confirm password")
        confirm_label.setFont(field_font)
        self.password2_input = QLineEdit()
        self.password2_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password2_input.setFont(field_font)
        self.password2_input.setMinimumHeight(36)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setFont(field_font)
        self.status_label.setWordWrap(True)

        self.register_button = QPushButton("Register")
        self.register_button.setFont(field_font)
        self.register_button.setMinimumHeight(40)
        self.register_button.clicked.connect(self.handle_register)

        self.login_link = QPushButton("Already have an account? Log in")
        self.login_link.setFont(field_font)
        self.login_link.setFlat(True)
        self.login_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_link.clicked.connect(self._handle_login_click)

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(10)
        layout.addWidget(title_label)
        layout.addSpacing(12)
        layout.addWidget(email_label)
        layout.addWidget(self.email_input)
        layout.addWidget(full_name_label)
        layout.addWidget(self.full_name_input)
        layout.addWidget(password_label)
        layout.addWidget(self.password1_input)
        layout.addWidget(confirm_label)
        layout.addWidget(self.password2_input)
        layout.addSpacing(8)
        layout.addWidget(self.register_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.login_link)
        layout.addStretch()
        self.setLayout(layout)

    def handle_register(self):
        email = self.email_input.text().strip()
        full_name = self.full_name_input.text().strip()
        password1 = self.password1_input.text()
        password2 = self.password2_input.text()

        if not email or not password1 or not password2:
            self.status_label.setText("Email and password are required.")
            return

        self.register_button.setEnabled(False)
        self.status_label.setText("")

        try:
            self.api_client.register(email, full_name, password1, password2)
        except ApiError as exc:
            self.status_label.setText(exc.message)
        else:
            QMessageBox.information(
                self,
                "Account created",
                "Account created successfully. You can now log in.",
            )
            if self.on_registered:
                self.on_registered()
        finally:
            self.register_button.setEnabled(True)

    def _handle_login_click(self):
        if self.on_login_requested:
            self.on_login_requested()
