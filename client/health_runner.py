import time

from PySide6.QtCore import QObject, QTimer

from .api_client import ApiError
from .workers import long_task_running, run_in_background

HEALTH_INTERVAL_MS = 5 * 60 * 1000  # how often to re-check while someone is logged in
MIN_GAP_SECONDS = 30  # opening screens in quick succession must not hammer the clouds


def check_all(api_client):
    """
    Ask the server to re-check every provider that is meant to be in use, and return
    the providers as they are afterwards. A provider that was disconnected on purpose
    is left alone. If one check can't be completed, that provider keeps the status it
    had, so one slow cloud doesn't hide the answers about the others.
    """
    results = []
    for provider in api_client.list_providers():
        if provider["status"] != "disconnected":
            try:
                provider = api_client.check_provider(provider["id"])
            except ApiError:
                pass
        results.append(provider)
    return results


class HealthRunner(QObject):
    """
    While someone is logged in, re-checks the connected providers now and then, so a
    provider that goes down (or comes back) shows up without pressing Check.

    on_result(problems, changed) is called after every round:
      problems  names of the providers that couldn't be reached
      changed   whether any provider's status differs from the previous round
    """

    def __init__(self, api_client, on_result=None, interval_ms=HEALTH_INTERVAL_MS):
        super().__init__()
        self.api_client = api_client
        self._on_result = on_result
        self._running = False
        self._last_started = None
        self._last_statuses = None  # {provider id: status}; None until the first round
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(lambda: self.check_now(force=True))

    def start(self, first_check_ms=2000):
        self._timer.start()
        if first_check_ms is not None:  # tests pass None to control exactly when rounds happen
            QTimer.singleShot(first_check_ms, self._first_check)

    def stop(self):
        self._timer.stop()
        self._last_statuses = None  # the next login starts from a clean slate

    def _first_check(self):
        if self._timer.isActive():
            self.check_now()

    def check_now(self, force=False):
        if (
            not self._timer.isActive()  # not started (or stopped by a logout)
            or self._running
            or self.api_client.token is None
            or long_task_running()
        ):
            return  # one round at a time; never when logged out or mid-backup
        recently = (
            self._last_started is not None
            and time.monotonic() - self._last_started < MIN_GAP_SECONDS
        )
        if recently and not force:
            return
        self._running = True
        self._last_started = time.monotonic()
        run_in_background(lambda: check_all(self.api_client), self._done, self._failed)

    def _failed(self, _message):
        # The server itself is unreachable. Other screens already say so when they
        # try to load, so there is nothing useful to add here.
        self._running = False

    def _done(self, providers):
        self._running = False
        if not self._timer.isActive():
            return  # the user logged out while the round was in flight
        statuses = {p["id"]: p["status"] for p in providers}
        changed = self._last_statuses is not None and statuses != self._last_statuses
        self._last_statuses = statuses
        problems = [p["display_name"] for p in providers if p["status"] == "error"]
        if self._on_result:
            self._on_result(problems, changed)
