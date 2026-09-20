import sys

from PySide6.QtWidgets import QApplication

from client.api_client import ApiClient
from client.windows.forgot_password_window import ForgotPasswordWindow
from client.windows.login_window import LoginWindow
from client.windows.main_window import MainWindow
from client.windows.register_window import RegisterWindow
from client.windows.reset_password_window import ResetPasswordWindow
from client.workers import set_unauthorized_handler

SESSION_EXPIRED = "Your session has expired. Please log in again."


class AppWindows:
    """Owns all top-level windows and switches between them."""

    def __init__(self, api_client: ApiClient):
        self.api_client = api_client
        self.login_window = LoginWindow(
            api_client,
            on_register_requested=self.show_register,
            on_forgot_password_requested=self.show_forgot_password,
            on_login_success=self.show_main,
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
        self.main_window = MainWindow(api_client, on_logout=self.show_login)
        self._windows = [
            self.login_window,
            self.register_window,
            self.forgot_password_window,
            self.reset_password_window,
            self.main_window,
        ]
        # Any screen whose request is refused with a 401 lands here.
        set_unauthorized_handler(self._session_expired)

    def _show_only(self, window):
        # Show the target first so there is never a moment with no visible window.
        window.show()
        for other in self._windows:
            if other is not window:
                other.hide()

    def show_login(self):
        self._show_only(self.login_window)

    def show_register(self):
        self._show_only(self.register_window)

    def show_forgot_password(self):
        self._show_only(self.forgot_password_window)

    def show_reset_password(self):
        self._show_only(self.reset_password_window)

    def show_main(self, user):
        self.main_window.set_user(user)
        self._show_only(self.main_window)

    def _session_expired(self):
        if not self.main_window.isVisible():
            return  # several requests can fail together; only the first needs to act
        self.api_client.token = None
        self.show_login()
        self.login_window.show_notice(SESSION_EXPIRED)

    def start(self):
        self.show_login()


def main():
    app = QApplication(sys.argv)
    try:
        api_client = ApiClient()
    except ValueError as exc:  # e.g. BACKUP_API_URL is plain http to a remote server
        sys.exit(str(exc))
    windows = AppWindows(api_client)
    windows.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
