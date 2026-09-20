from PySide6.QtCore import QObject, QTimer

from .scheduler import due_schedules, run_schedule
from .workers import long_task_running, run_in_background


class ScheduleRunner(QObject):
    """
    While the app is open and someone is logged in, checks every minute for
    schedules that are due and runs them, one at a time.

    on_status(text) reports what it is doing (empty when idle);
    on_finished(schedule, outcome) is called when a run ends, where outcome is
    the BackupResult, or an error message if the run couldn't be carried out.
    """

    def __init__(self, api_client, on_status=None, on_finished=None, interval_ms=60_000):
        super().__init__()
        self.api_client = api_client
        self._on_status = on_status
        self._on_finished = on_finished
        self._running = False
        self._current = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.check_now)

    def start(self):
        self._timer.start()
        QTimer.singleShot(3000, self._first_check)  # soon after opening, to catch up missed runs

    def stop(self):
        self._timer.stop()

    def _first_check(self):
        if self._timer.isActive():
            self.check_now()

    def check_now(self):
        # Never during a backup or restore the user started, and never when logged out.
        if self._running or self.api_client.token is None or long_task_running():
            return
        self._running = True
        run_in_background(lambda: due_schedules(self.api_client), self._found, self._check_failed)

    def _found(self, due):
        if not due:
            self._running = False
            self._status("")
            return

        schedule = self._current = due[0]
        self._status(f"Running scheduled backup '{schedule['name']}'…")
        run_in_background(
            lambda report: run_schedule(self.api_client, schedule, progress=report),
            on_success=self._done,
            on_error=self._failed,
            on_progress=self._progress,
        )

    def _check_failed(self, message):
        self._running = False
        self._status(f"Couldn't check scheduled backups: {message}")

    def _progress(self, stage, detail, done, total):
        if stage in ("protecting", "uploading") and total:
            name = self._current["name"]
            self._status(f"Scheduled backup '{name}': file {done + 1} of {total}")

    def _done(self, result):
        schedule = self._current
        self._running = False
        outcome = "completed" if result.status == "completed" else "finished with problems"
        self._status(f"Scheduled backup '{schedule['name']}' {outcome}")
        self._finished(schedule, result)
        self.check_now()  # another schedule may be due too

    def _failed(self, message):
        schedule = self._current
        self._running = False
        self._status(f"Scheduled backup '{schedule['name']}' failed: {message}")
        self._finished(schedule, message)  # no re-check: it was recorded and waits for next time

    def _status(self, text):
        if self._on_status:
            self._on_status(text)

    def _finished(self, schedule, outcome):
        if self._on_finished:
            self._on_finished(schedule, outcome)
