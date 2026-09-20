"""
Scheduled backups, on the client.

The files, and the encryption, are on this computer, so scheduled backups
run here too, while the app is open. The server holds the schedules and
decides when each is due; this module does the backing up and reports how
it went. A schedule that was due while the app was closed runs when the app
next opens (several missed times still mean one run).

Each run is a new, dated backup ("Docs 2026-09-21 02:00"), so files that came
or went since last time never leave stale entries behind.
"""

from datetime import datetime

from .api_client import ApiClient, ApiError
from .backup_service import BackupResult, run_backup


def due_schedules(api: ApiClient) -> list[dict]:
    return [schedule for schedule in api.list_schedules() if schedule["is_due"]]


def run_schedule(api: ApiClient, schedule: dict, progress=None) -> BackupResult:
    """Back up a schedule's source now and record how it went."""
    name = f"{schedule['name']} {datetime.now():%Y-%m-%d %H:%M}"
    try:
        result = run_backup(api, name, [schedule["source_path"]], progress=progress)
    except (OSError, ValueError, ApiError) as exc:
        # Recorded even so: a broken schedule (folder gone, too few providers) waits
        # for its next time instead of failing again every minute.
        api.record_schedule_run(schedule["id"], "failed", getattr(exc, "message", None) or str(exc))
        raise

    message = ""
    if result.status != "completed":
        message = f"{len(result.failed)} file(s) could not be backed up"
    api.record_schedule_run(schedule["id"], result.status, message)
    return result
