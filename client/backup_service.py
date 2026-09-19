"""
Client-side backup and restore: reads files, encrypts them locally, and
talks to the server through ApiClient. No GUI code lives here, so the
windows stay thin and this can be tested on its own.

`progress`, where accepted, is an optional callable(stage, detail) so a
window can show Preparing -> Protecting -> Uploading -> Completed (and
Downloading -> Decrypting -> Completed when restoring).
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


def _run_job(api: ApiClient, job: dict, progress) -> BackupResult:
    api.begin_job(job["id"])
    succeeded, failed = [], {}
    for file in job["files"]:
        try:
            _backup_file(api, file, progress)
            succeeded.append(file["original_path"])
        except (OSError, ApiError) as exc:
            # One bad file shouldn't abort the rest; the server marks the run failed.
            failed[file["original_path"]] = str(exc)

    finished = api.finish_job(job["id"])
    _report(progress, "completed", "")
    return BackupResult(
        job_id=job["id"], status=finished["status"], succeeded=succeeded, failed=failed
    )


def _backup_file(api: ApiClient, file: dict, progress) -> None:
    path = Path(file["original_path"])
    _report(progress, "protecting", str(path))
    plaintext = path.read_bytes()
    version, data_key = api.start_version(file["id"], path.name, len(plaintext))
    ciphertext = encrypt(plaintext, data_key)

    _report(progress, "uploading", str(path))
    api.upload_ciphertext(version["id"], ciphertext)


def _report(progress, stage: str, detail: str) -> None:
    if progress is not None:
        progress(stage, detail)
