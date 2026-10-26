from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from client.api_client import ApiClient
from client.workers import run_in_background

from .sets_page import describe_last_backup

MIN_PROVIDERS = 2
NO_VALUE = "–"
MAX_SET_SHORTCUTS = 3  # more than this would crowd the dashboard; the rest are one click away

# The getting-started steps: (what to do, the screen that does it, the button's text).
SETUP_STEPS = [
    ("Connect at least 2 cloud providers", "Cloud Providers", "Open Cloud Providers"),
    ("Run your first backup", "New Backup", "Open New Backup"),
    ("Restore a file to check that it works", "Recover Files", "Open Recover Files"),
]
DONE, CURRENT, LOCKED = "done", "current", "locked"


def setup_complete(counts) -> bool:
    """Setup is over once a backup and a restore have both succeeded."""
    return counts["backup_done"] and counts["restore_done"]


def setup_states(counts) -> list:
    """
    The state of each step in SETUP_STEPS: finished steps are DONE, the first
    unfinished one is CURRENT, and everything after it is LOCKED, because each
    step needs the one before it.
    """
    finished = [
        counts["connected"] >= MIN_PROVIDERS,
        counts["backup_done"],
        counts["restore_done"],
    ]
    states, found_current = [], False
    for step_done in finished:
        if step_done:
            states.append(DONE)
        elif not found_current:
            states.append(CURRENT)
            found_current = True
        else:
            states.append(LOCKED)
    return states


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


class _StepRow:
    """One line of the checklist: a mark, what to do, and a button that goes there."""

    def __init__(self, title, button_text, go):
        font = QFont()
        font.setPointSize(11)
        self.state = LOCKED
        self.mark = QLabel("")
        self.mark.setFont(font)
        self.mark.setFixedWidth(28)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(title)
        self.title.setFont(font)
        self.button = QPushButton(button_text)
        self.button.setFont(font)
        self.button.setMinimumHeight(32)
        self.button.clicked.connect(go)

        self.layout = QHBoxLayout()
        self.layout.addWidget(self.mark)
        self.layout.addWidget(self.title, 1)
        self.layout.addWidget(self.button)

    def show_state(self, number, state):
        self.state = state
        self.mark.setText("✓" if state == DONE else str(number))
        mark_color = {DONE: "#1b7f3b", CURRENT: "#1b5fa8", LOCKED: "#9a9a9a"}[state]
        self.mark.setStyleSheet(f"color: {mark_color}; font-weight: bold;")
        title_style = {
            DONE: "color: #6b6b6b;",
            CURRENT: "color: #000000; font-weight: bold;",
            LOCKED: "color: #9a9a9a;",
        }[state]
        self.title.setStyleSheet(title_style)
        self.button.setVisible(state != DONE)  # nothing left to do for a finished step
        self.button.setEnabled(state == CURRENT)


class DashboardPage(QWidget):
    def __init__(self, api_client: ApiClient, on_navigate=None, on_back_up=None):
        """
        on_navigate(screen_name) switches the app to that screen, e.g. "New Backup".
        on_back_up(backup_set) starts a backup of one saved set (a dict, as the server returns it).
        """
        super().__init__()
        self.api_client = api_client
        self._on_navigate = on_navigate
        self._on_back_up = on_back_up

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

        self.setup_frame = QFrame()
        self.setup_frame.setFrameShape(QFrame.Shape.StyledPanel)
        heading_font = QFont()
        heading_font.setPointSize(13)
        heading_font.setBold(True)
        heading = QLabel("Getting started")
        heading.setFont(heading_font)
        intro = QLabel("Three steps to get your files protected.")
        intro.setFont(body_font)
        intro.setStyleSheet("color: #555555;")
        setup_layout = QVBoxLayout(self.setup_frame)
        setup_layout.setContentsMargins(16, 16, 16, 16)
        setup_layout.setSpacing(10)
        setup_layout.addWidget(heading)
        setup_layout.addWidget(intro)
        self.step_rows = []
        for step_title, screen, button_text in SETUP_STEPS:
            row = _StepRow(step_title, button_text, lambda _checked=False, s=screen: self._go(s))
            self.step_rows.append(row)
            setup_layout.addLayout(row.layout)
        self.setup_frame.hide()  # shown once we know setup isn't finished

        self.sets_frame = QFrame()
        self.sets_frame.setFrameShape(QFrame.Shape.StyledPanel)
        sets_heading = QLabel("Back up now")
        sets_heading.setFont(heading_font)
        self.set_rows = QVBoxLayout()  # one row per saved set, rebuilt on every refresh
        self.set_rows.setSpacing(8)
        sets_layout = QVBoxLayout(self.sets_frame)
        sets_layout.setContentsMargins(16, 16, 16, 16)
        sets_layout.setSpacing(10)
        sets_layout.addWidget(sets_heading)
        sets_layout.addLayout(self.set_rows)
        self.set_buttons = []
        self.show_all_button = None
        self.sets_frame.hide()  # only when there is a saved set to offer

        self.status_label = QLabel("")
        self.status_label.setFont(body_font)
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(self.welcome_label)
        layout.addLayout(cards)
        layout.addWidget(self.setup_frame)
        layout.addWidget(self.sets_frame)
        layout.addWidget(self.status_label)
        layout.addStretch()

    def set_user(self, user):
        self.welcome_label.setText(f"Welcome, {user.get('full_name') or user['email']}.")

    def refresh(self):
        run_in_background(self._fetch, self._loaded, self._failed)

    def _fetch(self):
        providers = self.api_client.list_providers()
        jobs = self.api_client.list_jobs()
        recoveries = self.api_client.list_recoveries()
        return {
            "sets": self.api_client.list_sets(),
            "connected": sum(1 for p in providers if p["status"] == "connected"),
            "jobs": len(jobs),
            "recoveries": len(recoveries),
            # A created or failed backup doesn't count: only a finished one does.
            "backup_done": any(job["status"] == "completed" for job in jobs),
            "restore_done": any(r["status"] == "completed" for r in recoveries),
        }

    def _loaded(self, counts):
        self.providers_value.setText(str(counts["connected"]))
        self.jobs_value.setText(str(counts["jobs"]))
        self.recoveries_value.setText(str(counts["recoveries"]))

        self._apply_setup(counts)
        self._apply_sets(counts["sets"])
        if not setup_complete(counts):
            self.status_label.setText("")  # the checklist already says what to do next
        elif counts["connected"] < MIN_PROVIDERS:
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

    def _apply_setup(self, counts):
        self.setup_frame.setVisible(not setup_complete(counts))
        for number, (row, state) in enumerate(zip(self.step_rows, setup_states(counts)), start=1):
            row.show_state(number, state)

    def _apply_sets(self, sets):
        """Show a Back up now row for the first few saved sets, or nothing if there are none."""
        while self.set_rows.count():  # forget last time's rows
            item = self.set_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._delete_layout(item.layout())
        self.set_buttons = []
        self.show_all_button = None
        self.sets_frame.setVisible(bool(sets))

        font = QFont()
        font.setPointSize(11)
        for backup_set in sets[:MAX_SET_SHORTCUTS]:
            name = QLabel(backup_set["name"])
            name.setFont(font)
            name.setStyleSheet("font-weight: bold;")
            when = QLabel(f"Last backed up: {describe_last_backup(backup_set)}")
            when.setFont(font)
            when.setStyleSheet("color: #555555;")
            button = QPushButton("Back up now")
            button.setFont(font)
            button.setMinimumHeight(32)
            button.clicked.connect(lambda _checked=False, s=backup_set: self._back_up(s))
            self.set_buttons.append(button)
            row = QHBoxLayout()
            row.addWidget(name)
            row.addWidget(when, 1)
            row.addWidget(button)
            self.set_rows.addLayout(row)

        if len(sets) > MAX_SET_SHORTCUTS:
            self.show_all_button = QPushButton(f"Show all {len(sets)} sets")
            self.show_all_button.setFont(font)
            self.show_all_button.clicked.connect(lambda _checked=False: self._go("Backup Sets"))
            self.set_rows.addWidget(self.show_all_button)

    @staticmethod
    def _delete_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _back_up(self, backup_set):
        if self._on_back_up:
            self._on_back_up(backup_set)

    def _go(self, screen):
        if self._on_navigate:
            self._on_navigate(screen)

    def _failed(self, message):
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setText(message)
