import sys

from PySide6.QtWidgets import QApplication

from client.api_client import ApiClient
from client.windows.login_window import LoginWindow
from client.windows.register_window import RegisterWindow


class AppWindows:
    """Owns the login/register windows and switches between them."""

    def __init__(self, api_client: ApiClient):
        self.api_client = api_client
        self.login_window = LoginWindow(api_client, on_register_requested=self.show_register)
        self.register_window = RegisterWindow(
            api_client,
            on_registered=self.show_login,
            on_login_requested=self.show_login,
        )

    def show_login(self):
        self.register_window.hide()
        self.login_window.show()

    def show_register(self):
        self.login_window.hide()
        self.register_window.show()

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
