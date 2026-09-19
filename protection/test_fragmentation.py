import os

from django.test import SimpleTestCase

from .crypto import compute_sha256, protect_file_bytes, unprotect_file_bytes, verify_integrity
from .fragmentation import InsufficientFragmentsError, reconstruct, split_with_parity


class SplitAndReconstructTests(SimpleTestCase):
    def test_roundtrip_with_all_fragments(self):
        for n in range(2, 6):
            for length in (0, 1, 2, 7, 100, 4096, 100_003):
                with self.subTest(n=n, length=length):
                    data = os.urandom(length)
                    fragments, meta = split_with_parity(data, n)
                    self.assertEqual(len(fragments), n)
                    self.assertEqual(reconstruct(dict(enumerate(fragments)), meta), data)

    def test_any_single_fragment_can_be_lost(self):
        for n in range(2, 6):
            data = os.urandom(10_001)
            fragments, meta = split_with_parity(data, n)
            for lost in range(n):
                with self.subTest(n=n, lost=lost):
                    available = {i: f for i, f in enumerate(fragments) if i != lost}
                    self.assertEqual(reconstruct(available, meta), data)

    def test_losing_two_fragments_is_unrecoverable(self):
        fragments, meta = split_with_parity(os.urandom(1000), 3)
        with self.assertRaises(InsufficientFragmentsError):
            reconstruct({0: fragments[0]}, meta)

        fragments, meta = split_with_parity(os.urandom(1000), 4)
        with self.assertRaises(InsufficientFragmentsError):
            reconstruct({0: fragments[0], 3: fragments[3]}, meta)

    def test_parity_is_xor_of_data_fragments(self):
        fragments, _ = split_with_parity(os.urandom(999), 3)
        a, b, parity = (int.from_bytes(f, "big") for f in fragments)
        self.assertEqual(a ^ b, parity)

    def test_all_fragments_are_equal_size(self):
        fragments, meta = split_with_parity(os.urandom(1001), 4)
        self.assertEqual({len(f) for f in fragments}, {meta["fragment_size"]})

    def test_no_single_fragment_holds_the_whole_input_with_three_or_more(self):
        data = os.urandom(3000)
        fragments, _ = split_with_parity(data, 3)
        for fragment in fragments:
            self.assertLess(len(fragment), len(data))

    def test_fewer_than_two_fragments_is_rejected(self):
        with self.assertRaises(ValueError):
            split_with_parity(b"data", 1)

    def test_wrong_size_fragment_is_rejected(self):
        fragments, meta = split_with_parity(os.urandom(100), 3)
        fragments[0] = fragments[0][:-1]
        with self.assertRaises(ValueError):
            reconstruct(dict(enumerate(fragments)), meta)


class ProtectFragmentRecoverTests(SimpleTestCase):
    def test_full_pipeline_survives_any_one_provider_lost(self):
        plaintext = b"Contents of a file the user wants to back up." * 50
        protected = protect_file_bytes(plaintext)
        fragments, meta = split_with_parity(protected["ciphertext"], 3)

        for lost in range(3):
            with self.subTest(lost=lost):
                available = {i: f for i, f in enumerate(fragments) if i != lost}
                ciphertext = reconstruct(available, meta)
                self.assertTrue(verify_integrity(ciphertext, protected["ciphertext_hash"]))
                recovered = unprotect_file_bytes(ciphertext, protected["protection_metadata"])
                self.assertEqual(recovered, plaintext)

    def test_corrupted_fragment_is_caught_by_its_hash_and_recovery_still_works(self):
        plaintext = os.urandom(5000)
        protected = protect_file_bytes(plaintext)
        fragments, meta = split_with_parity(protected["ciphertext"], 3)
        stored_hashes = [compute_sha256(f) for f in fragments]

        retrieved = list(fragments)
        corrupted = bytearray(retrieved[1])
        corrupted[10] ^= 0xFF
        retrieved[1] = bytes(corrupted)

        available = {
            i: f for i, f in enumerate(retrieved) if verify_integrity(f, stored_hashes[i])
        }
        self.assertEqual(set(available), {0, 2})

        ciphertext = reconstruct(available, meta)
        self.assertEqual(unprotect_file_bytes(ciphertext, protected["protection_metadata"]), plaintext)
