"""
Backup and Recovery Manager: the server half of the backup pipeline.

The desktop client encrypts and decrypts, so plaintext never reaches the
server. The server hands the client a data key, receives ciphertext,
splits it across whichever providers are healthy right now, and records
where every fragment lives along with its hash. Recovery reverses that:
download what is available, verify hashes, rebuild the ciphertext, and
give it back to the client to decrypt.

Backup, per file:   start_file_version -> (client encrypts) -> store_ciphertext
Recovery, per file: start_recovery -> recover_ciphertext + recovery_data_key
                    -> (client decrypts) -> complete_recovery / fail_recovery
"""

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from monitoring.models import ActivityLog
from monitoring.service import log_activity
from protection.crypto import (
    ENCRYPTION_ALGORITHM,
    compute_sha256,
    generate_data_key,
    unwrap_key,
    verify_integrity,
    wrap_key,
)
from protection.fragmentation import InsufficientFragmentsError, reconstruct, split_with_parity
from protection.models import IntegrityRecord
from providers.adapters.base import ProviderError
from providers.manager import check_provider, get_adapter
from providers.models import CloudProvider, StorageLocation

from .models import BackupJob, File, FileVersion, RecoveryOperation

# With a single provider there is no redundancy, which defeats the point
# of a multi-cloud backup, so it is refused rather than silently allowed.
MIN_PROVIDERS = 2


class BackupError(Exception):
    """A backup could not be completed."""


class NotEnoughProvidersError(BackupError):
    """Fewer than MIN_PROVIDERS providers are currently reachable."""


class RecoveryError(Exception):
    """A recovery could not be completed."""


# ---------------------------------------------------------------- backup


def create_backup_job(user, name: str, paths: list[str]) -> BackupJob:
    """Record which files a backup covers. The client expands folders into file paths."""
    unique_paths = list(dict.fromkeys(paths))
    if not unique_paths:
        raise ValueError("Nothing to back up: no files were selected.")
    job = BackupJob.objects.create(user=user, name=name)
    File.objects.bulk_create([File(backup_job=job, original_path=p) for p in unique_paths])
    return job


def begin_backup_run(job: BackupJob) -> None:
    job.status = BackupJob.Status.IN_PROGRESS
    job.last_run_started_at = timezone.now()
    job.save(update_fields=["status", "last_run_started_at"])
    log_activity(job.user, "backup_run_started", f"Backup '{job.name}' started.", job_id=job.id)


def finish_backup_run(job: BackupJob) -> BackupJob:
    """
    A run succeeds only if every file got a completed version during this
    run. Judged from versions the server itself recorded, not from anything
    the client claims: a file the client skipped still has an older completed
    version, which must not make the run look successful.
    """
    since = job.last_run_started_at or job.created_at
    succeeded = all(
        file.versions.filter(
            upload_status=FileVersion.UploadStatus.COMPLETED, backup_time__gte=since
        ).exists()
        for file in job.files.all()
    )

    job.status = BackupJob.Status.COMPLETED if succeeded else BackupJob.Status.FAILED
    job.save(update_fields=["status"])
    log_activity(
        job.user,
        "backup_run_completed" if succeeded else "backup_run_failed",
        f"Backup '{job.name}' {'completed' if succeeded else 'failed'}.",
        ActivityLog.Level.INFO if succeeded else ActivityLog.Level.ERROR,
        job_id=job.id,
    )
    return job


def start_file_version(
    file: File, original_filename: str, original_size: int
) -> tuple[FileVersion, bytes]:
    """
    Create the next version of a file and issue its data key. The plaintext
    key goes to the client and is never stored; only the wrapped form is.
    """
    latest = file.versions.order_by("-version_number").first()
    data_key = generate_data_key()
    version = FileVersion.objects.create(
        file=file,
        version_number=latest.version_number + 1 if latest else 1,
        original_filename=original_filename,
        original_size=original_size,
        protection_metadata={"algorithm": ENCRYPTION_ALGORITHM, "wrapped_key": wrap_key(data_key)},
    )
    return version, data_key


def usable_providers(user) -> list[CloudProvider]:
    """The user's providers that pass a health check right now, in a stable order."""
    candidates = user.cloud_providers.exclude(status=CloudProvider.Status.DISCONNECTED)
    return [provider for provider in candidates.order_by("id") if check_provider(provider)]


def store_ciphertext(version: FileVersion, ciphertext: bytes) -> None:
    """Split the ciphertext across the healthy providers and record where it went."""
    user = version.file.backup_job.user
    version.upload_status = FileVersion.UploadStatus.UPLOADING
    version.save(update_fields=["upload_status"])

    providers = usable_providers(user)
    if len(providers) < MIN_PROVIDERS:
        message = (
            f"Need at least {MIN_PROVIDERS} reachable providers to back up, "
            f"found {len(providers)}."
        )
        _fail_version(version, message)
        raise NotEnoughProvidersError(message)

    fragments, fragmentation = split_with_parity(ciphertext, len(providers))

    uploaded = []
    try:
        for index, (provider, fragment) in enumerate(zip(providers, fragments)):
            remote_path = _remote_path(version, index)
            get_adapter(provider).upload(remote_path, fragment)
            uploaded.append((provider, remote_path))
            StorageLocation.objects.create(
                file_version=version,
                cloud_provider=provider,
                fragment_index=index,
                remote_path=remote_path,
                size_bytes=len(fragment),  # known now; recorded so storage can be totalled later
                upload_status=StorageLocation.UploadStatus.COMPLETED,
                uploaded_at=timezone.now(),
            )
            IntegrityRecord.objects.create(
                file_version=version,
                scope=IntegrityRecord.Scope.FRAGMENT,
                fragment_index=index,
                hash_value=compute_sha256(fragment),
            )
    except ProviderError as exc:
        # A backup that can't reach its full redundancy isn't reported as
        # successful, and half-uploaded fragments shouldn't be left behind.
        for provider, remote_path in uploaded:
            _delete_quietly(provider, remote_path)
        StorageLocation.objects.filter(file_version=version).update(
            upload_status=StorageLocation.UploadStatus.FAILED
        )
        IntegrityRecord.objects.filter(file_version=version).delete()
        message = f"Upload failed: {exc}"
        _fail_version(version, message)
        raise BackupError(message) from exc

    IntegrityRecord.objects.create(
        file_version=version,
        scope=IntegrityRecord.Scope.WHOLE_FILE,
        hash_value=compute_sha256(ciphertext),
    )
    version.protection_metadata = {**version.protection_metadata, "fragmentation": fragmentation}
    version.upload_status = FileVersion.UploadStatus.COMPLETED
    version.save(update_fields=["protection_metadata", "upload_status"])
    log_activity(
        user,
        "file_version_stored",
        f"Stored {version} across {len(providers)} providers.",
        version_id=version.id,
    )


def _remote_path(version: FileVersion, index: int) -> str:
    # Ids only: providers never learn the original filename or folder layout.
    file = version.file
    return (
        f"u{file.backup_job.user_id}/job{file.backup_job_id}/file{file.id}"
        f"/v{version.version_number}/part{index}.bin"
    )


def _delete_quietly(provider: CloudProvider, remote_path: str) -> None:
    try:
        get_adapter(provider).delete(remote_path)
    except ProviderError:
        pass


def _fail_version(version: FileVersion, message: str) -> None:
    version.upload_status = FileVersion.UploadStatus.FAILED
    version.save(update_fields=["upload_status"])
    log_activity(
        version.file.backup_job.user,
        "file_version_failed",
        message,
        ActivityLog.Level.ERROR,
        version_id=version.id,
    )


# -------------------------------------------------------------- recovery


def start_recovery(user, version: FileVersion) -> RecoveryOperation:
    _authorize(user, version)
    if version.upload_status != FileVersion.UploadStatus.COMPLETED:
        raise RecoveryError("This version was not backed up completely and cannot be recovered.")
    operation = RecoveryOperation.objects.create(
        user=user, file_version=version, status=RecoveryOperation.Status.IN_PROGRESS
    )
    log_activity(
        user, "recovery_started", f"Recovery of {version} started.", operation_id=operation.id
    )
    return operation


def recover_ciphertext(operation: RecoveryOperation) -> bytes:
    """
    Download the available fragments, keep only those matching their stored
    hash, rebuild the ciphertext, and check it against the whole-file hash.
    """
    _require_in_progress(operation)
    version = operation.file_version

    fragment_records = {
        record.fragment_index: record
        for record in version.integrity_records.filter(scope=IntegrityRecord.Scope.FRAGMENT)
    }
    whole_record = version.integrity_records.filter(
        scope=IntegrityRecord.Scope.WHOLE_FILE
    ).first()
    fragmentation = version.protection_metadata.get("fragmentation")
    if whole_record is None or fragmentation is None:
        _fail(operation, "No integrity information is stored for this version.")

    available = {}
    locations = version.storage_locations.filter(
        upload_status=StorageLocation.UploadStatus.COMPLETED
    ).select_related("cloud_provider")
    for location in locations:
        try:
            data = get_adapter(location.cloud_provider).download(location.remote_path)
        except ProviderError:
            continue  # provider down or disconnected: parity may cover for it

        record = fragment_records.get(location.fragment_index)
        valid = record is not None and verify_integrity(data, record.hash_value)
        if record is not None:
            record.is_valid = valid
            record.verified_at = timezone.now()
            record.save(update_fields=["is_valid", "verified_at"])
        if valid:
            available[location.fragment_index] = data

    try:
        ciphertext = reconstruct(available, fragmentation)
    except InsufficientFragmentsError as exc:
        _fail(operation, f"Could not gather enough intact fragments: {exc}", exc)

    intact = verify_integrity(ciphertext, whole_record.hash_value)
    whole_record.is_valid = intact
    whole_record.verified_at = timezone.now()
    whole_record.save(update_fields=["is_valid", "verified_at"])
    if not intact:
        _fail(operation, "The rebuilt data does not match its stored hash.")

    return ciphertext


def recovery_data_key(operation: RecoveryOperation) -> bytes:
    """Release the data key, but only for a recovery that is in progress."""
    _require_in_progress(operation)
    return unwrap_key(operation.file_version.protection_metadata["wrapped_key"])


def complete_recovery(operation: RecoveryOperation) -> None:
    """Called once the client has decrypted and restored the file."""
    operation.status = RecoveryOperation.Status.COMPLETED
    operation.completed_at = timezone.now()
    operation.save(update_fields=["status", "completed_at"])
    log_activity(
        operation.user,
        "recovery_completed",
        f"Recovery of {operation.file_version} completed.",
        operation_id=operation.id,
    )


def fail_recovery(operation: RecoveryOperation, message: str) -> None:
    operation.status = RecoveryOperation.Status.FAILED
    operation.error_message = message
    operation.completed_at = timezone.now()
    operation.save(update_fields=["status", "error_message", "completed_at"])
    log_activity(
        operation.user,
        "recovery_failed",
        message,
        ActivityLog.Level.ERROR,
        operation_id=operation.id,
    )


def _authorize(user, version: FileVersion) -> None:
    if version.file.backup_job.user_id != user.id:
        raise PermissionDenied("You do not have access to this backup.")


def _require_in_progress(operation: RecoveryOperation) -> None:
    if operation.status != RecoveryOperation.Status.IN_PROGRESS:
        raise RecoveryError("This recovery is not in progress.")


def _fail(operation: RecoveryOperation, message: str, cause=None):
    fail_recovery(operation, message)
    raise RecoveryError(message) from cause
