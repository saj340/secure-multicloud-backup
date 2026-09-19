"""
Storage adapter backed by a folder on disk.

Lets the whole backup pipeline run and be tested without any cloud
account, and makes "provider outage" trivial to demonstrate: delete or
rename the folder.
"""

import os
from pathlib import Path

from .base import ObjectNotFoundError, ProviderUnavailableError, StorageAdapter


class LocalFolderAdapter(StorageAdapter):
    def __init__(self, root):
        self.root = Path(root)

    def _require_root(self) -> None:
        if not self.root.is_dir():
            raise ProviderUnavailableError(f"Provider folder is not available: {self.root}")

    def _resolve(self, remote_path: str) -> Path:
        root = self.root.resolve()
        target = (root / remote_path).resolve()
        if target == root or not target.is_relative_to(root):
            raise ValueError(f"Invalid remote path: {remote_path!r}")
        return target

    def upload(self, remote_path: str, data: bytes) -> None:
        target = self._resolve(remote_path)
        self._require_root()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write then rename, so a failed upload never leaves a half-written object.
            temp = target.with_name(target.name + ".tmp")
            temp.write_bytes(data)
            os.replace(temp, target)
        except OSError as exc:
            raise ProviderUnavailableError(f"Could not write to {self.root}: {exc}") from exc

    def download(self, remote_path: str) -> bytes:
        target = self._resolve(remote_path)
        self._require_root()
        if not target.is_file():
            raise ObjectNotFoundError(f"No object at {remote_path!r}")
        try:
            return target.read_bytes()
        except OSError as exc:
            raise ProviderUnavailableError(f"Could not read from {self.root}: {exc}") from exc

    def delete(self, remote_path: str) -> None:
        target = self._resolve(remote_path)
        self._require_root()
        target.unlink(missing_ok=True)

    def exists(self, remote_path: str) -> bool:
        target = self._resolve(remote_path)
        self._require_root()
        return target.is_file()

    def health_check(self) -> bool:
        return self.root.is_dir() and os.access(self.root, os.W_OK)
