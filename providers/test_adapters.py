import os
import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from protection.crypto import (
    compute_sha256,
    protect_file_bytes,
    unprotect_file_bytes,
    verify_integrity,
)
from protection.fragmentation import reconstruct, split_with_parity

from .adapters.base import (
    ObjectNotFoundError,
    ProviderError,
    ProviderUnavailableError,
    StorageAdapter,
)
from .adapters.local import LocalFolderAdapter


class LocalFolderAdapterTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "provider"
        self.root.mkdir()
        self.adapter = LocalFolderAdapter(self.root)

    def test_is_a_storage_adapter_and_the_interface_cannot_be_instantiated(self):
        self.assertIsInstance(self.adapter, StorageAdapter)
        with self.assertRaises(TypeError):
            StorageAdapter()

    def test_upload_then_download_returns_identical_bytes(self):
        data = os.urandom(10_000)
        self.adapter.upload("backups/file1/v1.part", data)
        self.assertEqual(self.adapter.download("backups/file1/v1.part"), data)

    def test_upload_overwrites_existing_object(self):
        self.adapter.upload("a.bin", b"first")
        self.adapter.upload("a.bin", b"second")
        self.assertEqual(self.adapter.download("a.bin"), b"second")

    def test_upload_leaves_no_temp_files_behind(self):
        self.adapter.upload("a.bin", b"data")
        self.assertEqual([p.name for p in self.root.iterdir()], ["a.bin"])

    def test_exists(self):
        self.assertFalse(self.adapter.exists("a.bin"))
        self.adapter.upload("a.bin", b"data")
        self.assertTrue(self.adapter.exists("a.bin"))

    def test_delete_removes_object_and_is_idempotent(self):
        self.adapter.upload("a.bin", b"data")
        self.adapter.delete("a.bin")
        self.assertFalse(self.adapter.exists("a.bin"))
        self.adapter.delete("a.bin")

    def test_download_missing_object_raises_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            self.adapter.download("nope.bin")

    def test_paths_escaping_the_provider_folder_are_rejected(self):
        outside = self.root.parent / "evil.bin"
        for bad_path in ("../evil.bin", "a/../../evil.bin", str(outside), "", "."):
            with self.subTest(path=bad_path):
                with self.assertRaises(ValueError):
                    self.adapter.upload(bad_path, b"x")
                with self.assertRaises(ValueError):
                    self.adapter.download(bad_path)
        self.assertFalse(outside.exists())

    def test_health_check_reflects_folder_availability(self):
        self.assertTrue(self.adapter.health_check())
        shutil.rmtree(self.root)
        self.assertFalse(self.adapter.health_check())

    def test_operations_raise_unavailable_when_folder_is_gone(self):
        self.adapter.upload("a.bin", b"data")
        shutil.rmtree(self.root)
        with self.assertRaises(ProviderUnavailableError):
            self.adapter.upload("b.bin", b"data")
        with self.assertRaises(ProviderUnavailableError):
            self.adapter.download("a.bin")
        with self.assertRaises(ProviderUnavailableError):
            self.adapter.exists("a.bin")


class BackupSurvivesOutageTests(SimpleTestCase):
    """The project's demo scenario, using local folders as three providers."""

    def test_backup_and_recovery_with_one_provider_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / f"provider{i}" for i in range(3)]
            for root in roots:
                root.mkdir()
            adapters = [LocalFolderAdapter(root) for root in roots]

            plaintext = os.urandom(20_000)
            protected = protect_file_bytes(plaintext)
            fragments, meta = split_with_parity(protected["ciphertext"], 3)
            fragment_hashes = [compute_sha256(f) for f in fragments]

            for adapter, fragment in zip(adapters, fragments):
                adapter.upload("backups/file1/v1.part", fragment)

            shutil.rmtree(roots[1])

            available = {}
            for index, adapter in enumerate(adapters):
                try:
                    data = adapter.download("backups/file1/v1.part")
                except ProviderError:
                    continue
                if verify_integrity(data, fragment_hashes[index]):
                    available[index] = data

            self.assertEqual(set(available), {0, 2})
            ciphertext = reconstruct(available, meta)
            self.assertTrue(verify_integrity(ciphertext, protected["ciphertext_hash"]))
            recovered = unprotect_file_bytes(ciphertext, protected["protection_metadata"])
            self.assertEqual(recovered, plaintext)
