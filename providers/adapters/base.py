"""
Common interface every storage provider adapter implements.

Provider-specific code (SDKs, authentication, error types) stays inside
the adapter, so backup and recovery logic only ever talks to this
interface and providers can be added or swapped without touching it.
"""

from abc import ABC, abstractmethod


class ProviderError(Exception):
    """Base class for storage provider failures."""


class ProviderUnavailableError(ProviderError):
    """The provider can't be reached or used right now."""


class ObjectNotFoundError(ProviderError):
    """The requested remote object doesn't exist."""


class StorageAdapter(ABC):
    @abstractmethod
    def upload(self, remote_path: str, data: bytes) -> None:
        """Store data at remote_path, replacing anything already there."""

    @abstractmethod
    def download(self, remote_path: str) -> bytes:
        """Return the bytes stored at remote_path. Raises ObjectNotFoundError if absent."""

    @abstractmethod
    def delete(self, remote_path: str) -> None:
        """Remove remote_path. Deleting something that doesn't exist is not an error."""

    @abstractmethod
    def exists(self, remote_path: str) -> bool:
        """Whether an object is stored at remote_path."""

    @abstractmethod
    def health_check(self) -> bool:
        """Whether the provider is currently reachable and usable."""
