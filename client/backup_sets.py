"""
"Back up now" for a saved backup set.

A set remembers folders and files, not the files inside them. Running it looks inside
those places at that moment, so files added since last time are included, and each run
is its own dated backup ("Documents 2026-10-26 14:30"), the same way scheduled backups
work. How the run went is reported to the server so the set can show it.
"""

from datetime import datetime

from .api_client import ApiClient, ApiError
from .backup_service import BackupResult, run_backup


def run_set(api: ApiClient, backup_set: dict, progress=None) -> BackupResult:
    """Back up everything a set points at, right now, and record how it went."""
    name = f"{backup_set['name']} {datetime.now():%Y-%m-%d %H:%M}"
    try:
        result = run_backup(api, name, backup_set["sources"], progress=progress)
    except (OSError, ValueError, ApiError) as exc:
        # A folder that has gone, or too few providers: recorded, then shown to the user.
        api.record_set_run(backup_set["id"], "failed", getattr(exc, "message", None) or str(exc))
        raise

    message = ""
    if result.status != "completed":
        message = f"{len(result.failed)} file(s) could not be backed up"
    api.record_set_run(backup_set["id"], result.status, message)
    return result
