import sys

from PySide6.QtWidgets import QApplication

from client.api_client import ApiClient
from client.windows.login_window import LoginWindow


def main():
    app = QApplication(sys.argv)
    api_client = ApiClient()
    window = LoginWindow(api_client)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
