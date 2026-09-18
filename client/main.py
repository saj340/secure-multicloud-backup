import sys

from PySide6.QtWidgets import QApplication

from client.api_client import ApiClient
from client.windows.forgot_password_window import ForgotPasswordWindow
from client.windows.login_window import LoginWindow
from client.windows.register_window import RegisterWindow
from client.windows.reset_password_window import ResetPasswordWindow


class AppWindows:
    """Owns all top-level windows and switches between them."""

    def __init__(self, api_client: ApiClient):
        self.api_client = api_client
        self.login_window = LoginWindow(
            api_client,
            on_register_requested=self.show_register,
            on_forgot_password_requested=self.show_forgot_password,
        )
        self.register_window = RegisterWindow(
            api_client,
            on_registered=self.show_login,
            on_login_requested=self.show_login,
        )
        self.forgot_password_window = ForgotPasswordWindow(
            api_client,
            on_requested=self.show_reset_password,
            on_login_requested=self.show_login,
        )
        self.reset_password_window = ResetPasswordWindow(
            api_client,
            on_reset=self.show_login,
            on_login_requested=self.show_login,
        )
        self._windows = [
            self.login_window,
            self.register_window,
            self.forgot_password_window,
            self.reset_password_window,
        ]

    def _show_only(self, window):
        for w in self._windows:
            if w is window:
                w.show()
            else:
                w.hide()

    def show_login(self):
        self._show_only(self.login_window)

    def show_register(self):
        self._show_only(self.register_window)

    def show_forgot_password(self):
        self._show_only(self.forgot_password_window)

    def show_reset_password(self):
        self._show_only(self.reset_password_window)

    def start(self):
        self.show_login()


def main():
    app = QApplication(sys.argv)
    api_client = ApiClient()
    windows = AppWindows(api_client)
    windows.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
