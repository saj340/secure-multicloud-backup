"""
Client-side backup and restore: reads files, encrypts them locally, and
talks to the server through ApiClient. No GUI code lives here, so the
windows stay thin and this can be tested on its own.

`progress`, where accepted, is an optional callable(stage, detail, done,
total) so a window can show what is happening. Backing up reports
preparing, then for each file protecting and uploading (done = files
finished so far, total = files in the run) and processed once that file
is finished either way, then completed. Restoring reports downloading,
decrypting and completed, without counts.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from .api_client import ApiClient, ApiError
from .crypto import DecryptionFailed, decrypt, encrypt


@dataclass
class BackupResult:
    job_id: int
    status: str
    succeeded: list = field(default_factory=list)
    failed: dict = field(default_factory=dict)


@dataclass
class RestoreItem:
    source: str  # the file's original path, as it was backed up
    version: dict  # the version that will be restored
    target: Path  # where it will be written


@dataclass
class RestorePlan:
    """What restoring a whole backup would do, worked out before anything is written."""

    items: list = field(default_factory=list)
    unavailable: list = field(default_factory=list)  # files with no complete version stored
    unsafe: dict = field(default_factory=dict)  # source path -> why it was refused


@dataclass
class RestoreSummary:
    restored: list = field(default_factory=list)  # paths written
    skipped: list = field(default_factory=list)  # already there, left alone
    failed: dict = field(default_factory=dict)  # source path -> reason


def collect_files(paths) -> list[Path]:
    """Expand a selection of files and folders into a de-duplicated list of files."""
    files = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            files.append(path.resolve())
        elif path.is_dir():
            files.extend(sorted(p.resolve() for p in path.rglob("*") if p.is_file()))
        else:
            raise FileNotFoundError(f"Not a file or folder: {raw}")
    return list(dict.fromkeys(files))


def load_backups(api: ApiClient) -> list[dict]:
    """Every backup job with its files and their versions, newest job first."""
    return [api.get_job(job["id"]) for job in api.list_jobs()]


def run_backup(api: ApiClient, name: str, paths, progress=None) -> BackupResult:
    _report(progress, "preparing", "")
    files = collect_files(paths)
    if not files:
        raise ValueError("Nothing to back up: no files were found.")
    job = api.create_job(name, [str(f) for f in files])
    return _run_job(api, job, progress)


def rerun_backup(api: ApiClient, job_id: int, progress=None) -> BackupResult:
    """Back up the same files again; each one gets a new version."""
    _report(progress, "preparing", "")
    return _run_job(api, api.get_job(job_id), progress)


def restore_version(
    api: ApiClient, version_id: int, destination, overwrite=False, progress=None
) -> Path:
    """Recover one file version, decrypt it locally, and write it to destination."""
    destination = Path(destination)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"{destination} already exists.")

    operation_id = api.start_recovery(version_id)["id"]
    temp = destination.with_name(destination.name + ".part")
    try:
        _report(progress, "downloading", "")
        ciphertext = api.download_recovery_ciphertext(operation_id)
        data_key = api.get_recovery_key(operation_id)

        _report(progress, "decrypting", "")
        plaintext = decrypt(ciphertext, data_key)

        destination.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a failed restore never leaves a half-written file.
        temp.write_bytes(plaintext)
        os.replace(temp, destination)
    except (DecryptionFailed, OSError) as exc:
        # Failures on this side are invisible to the server unless reported;
        # server-side failures (ApiError) were already recorded by the server.
        temp.unlink(missing_ok=True)
        api.fail_recovery(operation_id, f"Could not restore the file: {exc}")
        raise

    api.complete_recovery(operation_id)
    _report(progress, "completed", str(destination))
    return destination


def _newest_completed(file: dict):
    """The file's newest version that was stored completely, or None."""
    completed = [v for v in file["versions"] if v["upload_status"] == "completed"]
    return max(completed, key=lambda v: v["version_number"]) if completed else None


def _relative_paths(sources) -> list[Path]:
    """
    Each path relative to the deepest folder that all of them share, so the folder
    layout comes back without the long path above it (C:\\Users\\me\\...).
    """
    paths = [Path(source) for source in sources]
    try:
        base = Path(os.path.commonpath([str(path.parent) for path in paths]))
        return [path.relative_to(base) for path in paths]
    except ValueError:
        # Files from different drives (C: and D:) have no folder in common, so the
        # drive letter becomes a top-level folder instead.
        return [Path(path.drive.rstrip(":\\/"), *path.parts[1:]) if path.anchor else path for path in paths]


def safe_target(destination: Path, relative: Path):
    """
    destination/relative, or None if that would land outside destination.

    The paths come from the server, so they are treated as untrusted: a path with ".."
    in it must never be able to write somewhere else on the computer.
    """
    if relative.is_absolute() or ".." in relative.parts:
        return None
    target = destination / relative
    try:
        target.resolve().relative_to(destination.resolve())
    except ValueError:
        return None
    return target


def plan_restore(job: dict, destination) -> RestorePlan:
    """Work out, without writing anything, where each file of a backup would be restored."""
    destination = Path(destination)
    plan = RestorePlan()
    files = job["files"]
    for file, relative in zip(files, _relative_paths([f["original_path"] for f in files])):
        version = _newest_completed(file)
        if version is None:
            plan.unavailable.append(file["original_path"])
            continue
        target = safe_target(destination, relative)
        if target is None:
            plan.unsafe[file["original_path"]] = "Refused: it would be written outside the chosen folder."
            continue
        plan.items.append(RestoreItem(file["original_path"], version, target))
    return plan


def restore_backup(
    api: ApiClient, job: dict, destination, overwrite=False, progress=None
) -> RestoreSummary:
    """
    Restore every file of a backup into destination, keeping its folder layout.

    A file that fails doesn't stop the others; the summary says what was restored, what
    was left alone because it already existed (unless overwrite is True), and what failed.
    Reports downloading for each file (done = files finished so far), then processed, then completed.
    """
    plan = plan_restore(job, destination)
    summary = RestoreSummary()
    for path in plan.unavailable:
        summary.failed[path] = "No complete version of this file was stored."
    summary.failed.update(plan.unsafe)

    total = len(plan.items)
    _report(progress, "preparing", "")
    for index, item in enumerate(plan.items):
        if item.target.exists() and not overwrite:
            summary.skipped.append(str(item.target))
        else:
            _report(progress, "downloading", str(item.target), index, total)
            try:
                restore_version(api, item.version["id"], item.target, overwrite=True)
                summary.restored.append(str(item.target))
            except (ApiError, DecryptionFailed, OSError) as exc:
                summary.failed[item.source] = getattr(exc, "message", None) or str(exc)
        _report(progress, "processed", str(item.target), index + 1, total)
    _report(progress, "completed", "", total, total)
    return summary


def _run_job(api: ApiClient, job: dict, progress) -> BackupResult:
    api.begin_job(job["id"])
    files = job["files"]
    total = len(files)
    succeeded, failed = [], {}
    for index, file in enumerate(files):
        path = file["original_path"]
        try:
            _backup_file(api, file, progress, index, total)
            succeeded.append(path)
        except (OSError, ApiError) as exc:
            # One bad file shouldn't abort the rest; the server marks the run failed.
            failed[path] = str(exc)
        _report(progress, "processed", path, index + 1, total)

    finished = api.finish_job(job["id"])
    _report(progress, "completed", "", total, total)
    return BackupResult(
        job_id=job["id"], status=finished["status"], succeeded=succeeded, failed=failed
    )


def _backup_file(api: ApiClient, file: dict, progress, index: int, total: int) -> None:
    path = Path(file["original_path"])
    _report(progress, "protecting", str(path), index, total)
    plaintext = path.read_bytes()
    version, data_key = api.start_version(file["id"], path.name, len(plaintext))
    ciphertext = encrypt(plaintext, data_key)

    _report(progress, "uploading", str(path), index, total)
    api.upload_ciphertext(version["id"], ciphertext)


def _report(progress, stage: str, detail: str, done=None, total=None) -> None:
    if progress is not None:
        progress(stage, detail, done, total)
