"""
Run blocking work (network calls) off the UI thread so windows never freeze.

    run_in_background(lambda: api.list_providers(), on_success=show, on_error=warn)

For long work that should report progress, pass on_progress; the function
then receives a `report` callable, and whatever it is called with is
handed to on_progress:

    run_in_background(lambda report: run_backup(..., progress=report),
                      on_success=done, on_progress=update_bar)

Call it from the UI thread. All callbacks are also delivered on the UI
thread, so they can touch widgets directly.
"""

from PySide6.QtCore import QObject, QThread, Signal, Slot

_active = set()


class _Thread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progressed = Signal(object)

    def __init__(self, fn, wants_progress):
        super().__init__()
        self._fn = fn
        self._wants_progress = wants_progress

    def run(self):
        try:
            if self._wants_progress:
                result = self._fn(lambda *args: self.progressed.emit(args))
            else:
                result = self._fn()
        except Exception as exc:  # a worker must never die silently; the UI reports it
            self.failed.emit(getattr(exc, "message", None) or str(exc))
        else:
            self.succeeded.emit(result)


class _Job(QObject):
    """Lives on the UI thread, so the callbacks it fires run there too."""

    def __init__(self, fn, on_success, on_error, on_progress):
        super().__init__()
        self._on_success = on_success
        self._on_error = on_error
        self._on_progress = on_progress
        self._thread = _Thread(fn, wants_progress=on_progress is not None)
        self._thread.succeeded.connect(self._handle_success)
        self._thread.failed.connect(self._handle_error)
        self._thread.progressed.connect(self._handle_progress)
        self._thread.finished.connect(self._cleanup)

    def start(self):
        self._thread.start()

    @Slot(object)
    def _handle_success(self, result):
        if self._on_success is not None:
            self._on_success(result)

    @Slot(str)
    def _handle_error(self, message):
        if self._on_error is not None:
            self._on_error(message)

    @Slot(object)
    def _handle_progress(self, args):
        self._on_progress(*args)

    @Slot()
    def _cleanup(self):
        _active.discard(self)
        self._thread.deleteLater()
        self.deleteLater()


def run_in_background(fn, on_success=None, on_error=None, on_progress=None):
    job = _Job(fn, on_success, on_error, on_progress)
    _active.add(job)  # keep the job alive until its thread has finished
    job.start()


def long_task_running() -> bool:
    """Whether a task that reports progress (a backup or restore) is in flight."""
    return any(job._on_progress is not None for job in _active)
