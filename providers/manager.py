"""
Cloud Provider Manager: connects, checks, and disconnects a user's storage
providers, and builds the right adapter for each one.

Credentials are encrypted with the master key before they touch the
database, so a leaked database row never contains a usable cloud
credential. Supporting a new provider means writing an adapter and
registering it in ADAPTER_FACTORIES; nothing else changes.
"""

import json

from django.utils import timezone

from protection.crypto import DecryptionError, decrypt_with_master_key, encrypt_with_master_key

from .adapters.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderUnavailableError,
    StorageAdapter,
)
from .adapters.local import LocalFolderAdapter
from .models import CloudProvider

ADAPTER_FACTORIES = {
    CloudProvider.ProviderType.LOCAL_FOLDER.value: lambda credentials: LocalFolderAdapter(
        credentials["path"]
    ),
}


def _build_adapter(provider_type: str, credentials: dict) -> StorageAdapter:
    factory = ADAPTER_FACTORIES.get(str(provider_type))
    if factory is None:
        raise ProviderConfigurationError(f"Unsupported provider type: {provider_type!r}")
    try:
        return factory(credentials)
    except KeyError as exc:
        raise ProviderConfigurationError(f"Missing credential field: {exc}") from exc


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
    return provider


def check_provider(provider: CloudProvider) -> bool:
    """Re-check a connected provider and record the result as its status."""
    if provider.status == CloudProvider.Status.DISCONNECTED:
        return False

    try:
        healthy = get_adapter(provider).health_check()
    except ProviderError:
        healthy = False

    provider.status = CloudProvider.Status.CONNECTED if healthy else CloudProvider.Status.ERROR
    provider.last_checked_at = timezone.now()
    provider.save(update_fields=["status", "last_checked_at"])
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
