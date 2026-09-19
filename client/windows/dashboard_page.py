from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from client.api_client import ApiClient
from client.workers import run_in_background

MIN_PROVIDERS = 2
NO_VALUE = "–"


def _card(caption: str):
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)

    value_font = QFont()
    value_font.setPointSize(26)
    value_font.setBold(True)
    value = QLabel(NO_VALUE)
    value.setFont(value_font)
    value.setAlignment(Qt.AlignmentFlag.AlignCenter)

    caption_font = QFont()
    caption_font.setPointSize(11)
    label = QLabel(caption)
    label.setFont(caption_font)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("color: #555555;")

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 20, 16, 20)
    layout.addWidget(value)
    layout.addWidget(label)
    return value, frame


class DashboardPage(QWidget):
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api_client = api_client

        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(11)

        title = QLabel("Dashboard")
        title.setFont(title_font)

        self.welcome_label = QLabel("")
        self.welcome_label.setFont(body_font)

        self.providers_value, providers_card = _card("Connected providers")
        self.jobs_value, jobs_card = _card("Backup jobs")
        self.recoveries_value, recoveries_card = _card("Recoveries")

        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (providers_card, jobs_card, recoveries_card):
            cards.addWidget(card)

        self.status_label = QLabel("")
        self.status_label.setFont(body_font)
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(self.welcome_label)
        layout.addLayout(cards)
        layout.addWidget(self.status_label)
        layout.addStretch()

    def set_user(self, user):
        self.welcome_label.setText(f"Welcome, {user.get('full_name') or user['email']}.")

    def refresh(self):
        run_in_background(self._fetch, self._loaded, self._failed)

    def _fetch(self):
        providers = self.api_client.list_providers()
        return {
            "connected": sum(1 for p in providers if p["status"] == "connected"),
            "jobs": len(self.api_client.list_jobs()),
            "recoveries": len(self.api_client.list_recoveries()),
        }

    def _loaded(self, counts):
        self.providers_value.setText(str(counts["connected"]))
        self.jobs_value.setText(str(counts["jobs"]))
        self.recoveries_value.setText(str(counts["recoveries"]))

        if counts["connected"] < MIN_PROVIDERS:
            self.status_label.setStyleSheet("color: #b26a00;")
            self.status_label.setText(
                f"Connect at least {MIN_PROVIDERS} cloud providers under Cloud Providers "
                "before you back anything up."
            )
        else:
            self.status_label.setStyleSheet("color: #1b7f3b;")
            self.status_label.setText(
                f"Ready: backups are spread across {counts['connected']} providers and "
                "survive any one of them being unavailable."
            )

    def _failed(self, message):
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setText(message)
