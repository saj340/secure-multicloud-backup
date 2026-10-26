"""
Cloud Provider Manager: connects, checks, and disconnects a user's storage
providers, and builds the right adapter for each one.

Credentials are encrypted with the master key before they touch the
database, so a leaked database row never contains a usable cloud
credential. Supporting a new provider means writing an adapter and
registering it in ADAPTER_FACTORIES; nothing else changes.
"""

import json

from django.conf import settings
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from monitoring.models import ActivityLog
from monitoring.service import log_activity
from protection.crypto import DecryptionError, decrypt_with_master_key, encrypt_with_master_key

from .adapters.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderUnavailableError,
    StorageAdapter,
)
from .adapters.local import LocalFolderAdapter
from .adapters.s3 import S3Adapter
from .endpoints import validate_endpoint_url
from .models import CloudProvider, StorageLocation


_FINISHED = Q(storage_locations__upload_status=StorageLocation.UploadStatus.COMPLETED)


def with_storage_used(providers):
    """
    Add `storage_used_bytes` to every provider in a queryset, in a single database
    query: the total size of the fragments it holds. Fragments of failed uploads
    don't count, because they were removed again.
    """
    return providers.annotate(
        storage_used_bytes=Coalesce(Sum("storage_locations__size_bytes", filter=_FINISHED), 0)
    )


def storage_used(provider: CloudProvider) -> int:
    """The same total for one provider that wasn't loaded through with_storage_used."""
    return with_storage_used(CloudProvider.objects.filter(pk=provider.pk)).get().storage_used_bytes


def _local_folder(credentials):
    return LocalFolderAdapter(credentials["path"])


def _aws_s3(credentials):
    # No endpoint is accepted here: AWS is always reached at its own addresses.
    if not credentials["region"]:
        raise ValueError("region")
    return S3Adapter(
        bucket=credentials["bucket"],
        access_key_id=credentials["access_key_id"],
        secret_access_key=credentials["secret_access_key"],
        region=credentials["region"],
    )


def _s3_compatible(credentials):
    endpoint_url = credentials["endpoint_url"]
    validate_endpoint_url(endpoint_url)
    return S3Adapter(
        bucket=credentials["bucket"],
        access_key_id=credentials["access_key_id"],
        secret_access_key=credentials["secret_access_key"],
        region=credentials.get("region"),
        endpoint_url=endpoint_url,
    )


ADAPTER_FACTORIES = {
    CloudProvider.ProviderType.LOCAL_FOLDER.value: _local_folder,
    CloudProvider.ProviderType.AWS_S3.value: _aws_s3,
    CloudProvider.ProviderType.S3_COMPATIBLE.value: _s3_compatible,
}


def available_provider_types() -> list[str]:
    """Provider types a user may connect through the API right now."""
    types = list(ADAPTER_FACTORIES)
    if not settings.ALLOW_LOCAL_FOLDER_PROVIDERS:
        types.remove(CloudProvider.ProviderType.LOCAL_FOLDER.value)
    return types


def _build_adapter(provider_type: str, credentials: dict) -> StorageAdapter:
    factory = ADAPTER_FACTORIES.get(str(provider_type))
    if factory is None:
        raise ProviderConfigurationError(f"Unsupported provider type: {provider_type!r}")
    try:
        return factory(credentials)
    except (KeyError, ValueError) as exc:
        raise ProviderConfigurationError(f"Missing or invalid credential: {exc}") from exc


def set_credentials(provider: CloudProvider, credentials: dict) -> None:
    """Encrypt credentials onto the provider. The caller is responsible for saving it."""
    provider.encrypted_credentials = encrypt_with_master_key(json.dumps(credentials).encode())


def get_credentials(provider: CloudProvider) -> dict:
    if not provider.encrypted_credentials:
        raise ProviderConfigurationError("This provider has no stored credentials.")
    try:
        return json.loads(decrypt_with_master_key(provider.encrypted_credentials))
    except DecryptionError as exc:
        raise ProviderConfigurationError("Stored credentials could not be decrypted.") from exc


def get_adapter(provider: CloudProvider) -> StorageAdapter:
    return _build_adapter(provider.provider_type, get_credentials(provider))


def connect_provider(user, provider_type: str, display_name: str, credentials: dict) -> CloudProvider:
    """
    Verify the credentials work, then store the provider with them
    encrypted. A provider that can't be reached is never saved, so the
    user hears about wrong credentials immediately.
    """
    adapter = _build_adapter(provider_type, credentials)
    if not adapter.health_check():
        raise ProviderUnavailableError("Could not reach the provider with the supplied credentials.")

    provider = CloudProvider(
        user=user,
        provider_type=provider_type,
        display_name=display_name,
        status=CloudProvider.Status.CONNECTED,
        last_checked_at=timezone.now(),
    )
    set_credentials(provider, credentials)
    provider.save()
    log_activity(
        user,
        "provider_connected",
        f"Connected {provider.get_provider_type_display()} '{display_name}'.",
        provider_id=provider.id,
    )
    return provider


# How many stored fragments a reconnect looks for. Enough to notice credentials that
# point at the wrong bucket, without a request per fragment for a large backup history.
RECONNECT_SAMPLE = 25


def reconnect_provider(provider: CloudProvider, credentials: dict):
    """
    Give a disconnected (or failing) provider working credentials again. The record is
    reused, not replaced, so the backup fragments already tied to it are reachable again.

    Returns (provider, checked, missing): how many of its most recent stored fragments
    were looked for, and how many were not there. A non-zero `missing` usually means the
    credentials lead to a different bucket or folder than the one the backups went to.
    """
    if provider.status == CloudProvider.Status.CONNECTED:
        raise ProviderConfigurationError("This provider is already connected.")

    adapter = _build_adapter(provider.provider_type, credentials)
    if not adapter.health_check():
        raise ProviderUnavailableError("Could not reach the provider with the supplied credentials.")

    set_credentials(provider, credentials)
    provider.status = CloudProvider.Status.CONNECTED
    provider.last_checked_at = timezone.now()
    provider.save()

    stored = provider.storage_locations.filter(
        upload_status=StorageLocation.UploadStatus.COMPLETED
    ).order_by("-id")[:RECONNECT_SAMPLE]
    checked = missing = 0
    for location in stored:
        checked += 1
        try:
            found = adapter.exists(location.remote_path)
        except ProviderError:
            found = False
        if not found:
            missing += 1

    log_activity(
        provider.user,
        "provider_reconnected",
        f"Reconnected '{provider.display_name}'."
        + (f" {missing} of {checked} stored fragments were not found there." if missing else ""),
        level=ActivityLog.Level.WARNING if missing else ActivityLog.Level.INFO,
        provider_id=provider.id,
    )
    return provider, checked, missing


def check_provider(provider: CloudProvider) -> bool:
    """Re-check a connected provider and record the result as its status."""
    if provider.status == CloudProvider.Status.DISCONNECTED:
        return False

    try:
        healthy = get_adapter(provider).health_check()
    except ProviderError:
        healthy = False

    was_healthy = provider.status == CloudProvider.Status.CONNECTED
    provider.status = CloudProvider.Status.CONNECTED if healthy else CloudProvider.Status.ERROR
    provider.last_checked_at = timezone.now()
    provider.save(update_fields=["status", "last_checked_at"])
    # Only a change is worth a log entry, or every dashboard refresh would add one.
    if healthy != was_healthy:
        log_activity(
            provider.user,
            "provider_recovered" if healthy else "provider_unreachable",
            f"'{provider.display_name}' is {'reachable again' if healthy else 'unreachable'}.",
            level=ActivityLog.Level.INFO if healthy else ActivityLog.Level.WARNING,
            provider_id=provider.id,
        )
    return healthy


def disconnect_provider(provider: CloudProvider) -> None:
    """
    Forget the provider's credentials and mark it disconnected. The record
    itself is kept, because deleting it would cascade away the storage
    locations that say where existing backup fragments live.
    """
    provider.encrypted_credentials = ""
    provider.status = CloudProvider.Status.DISCONNECTED
    provider.save(update_fields=["encrypted_credentials", "status"])
    log_activity(
        provider.user,
        "provider_disconnected",
        f"Disconnected '{provider.display_name}'.",
        provider_id=provider.id,
    )
