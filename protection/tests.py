from django.test import TestCase

from .crypto import (
    DecryptionError,
    compute_sha256,
    decrypt_bytes,
    decrypt_with_master_key,
    encrypt_bytes,
    encrypt_with_master_key,
    generate_data_key,
    protect_file_bytes,
    unprotect_file_bytes,
    unwrap_key,
    verify_integrity,
    wrap_key,
)


class EncryptionRoundTripTests(TestCase):
    def test_encrypt_then_decrypt_returns_original(self):
        plaintext = b"This is a test file's contents."
        key = generate_data_key()
        ciphertext = encrypt_bytes(plaintext, key)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(decrypt_bytes(ciphertext, key), plaintext)

    def test_ciphertext_does_not_contain_plaintext(self):
        plaintext = b"SECRET-MARKER-12345"
        key = generate_data_key()
        ciphertext = encrypt_bytes(plaintext, key)
        self.assertNotIn(b"SECRET-MARKER-12345", ciphertext)

    def test_wrong_key_fails_to_decrypt(self):
        plaintext = b"data"
        key1 = generate_data_key()
        key2 = generate_data_key()
        ciphertext = encrypt_bytes(plaintext, key1)
        with self.assertRaises(DecryptionError):
            decrypt_bytes(ciphertext, key2)

    def test_tampered_ciphertext_is_rejected(self):
        plaintext = b"important data"
        key = generate_data_key()
        ciphertext = bytearray(encrypt_bytes(plaintext, key))
        ciphertext[-5] ^= 0xFF
        with self.assertRaises(DecryptionError):
            decrypt_bytes(bytes(ciphertext), key)


class KeyWrappingTests(TestCase):
    def test_wrap_then_unwrap_returns_original_key(self):
        key = generate_data_key()
        wrapped = wrap_key(key)
        self.assertNotEqual(wrapped.encode(), key)
        self.assertEqual(unwrap_key(wrapped), key)

    def test_corrupted_wrapped_key_is_rejected(self):
        key = generate_data_key()
        wrapped = wrap_key(key)
        corrupted = wrapped[:10] + "X" + wrapped[11:]
        with self.assertRaises(DecryptionError):
            unwrap_key(corrupted)

    def test_garbage_input_raises_decryption_error_not_a_raw_exception(self):
        # Malformed input can fail before Fernet's HMAC check even runs
        # (e.g. invalid base64) -- this must still surface as our one
        # consistent DecryptionError, not a raw ValueError/binascii.Error.
        with self.assertRaises(DecryptionError):
            unwrap_key("not-valid-base64-at-all!!!")
        with self.assertRaises(DecryptionError):
            decrypt_bytes(b"not-valid-base64-at-all!!!", generate_data_key())


class ProtectFileBytesTests(TestCase):
    def test_full_protect_and_unprotect_roundtrip(self):
        plaintext = b"Contents of a file the user wants to back up."
        result = protect_file_bytes(plaintext)

        self.assertIn("ciphertext", result)
        self.assertIn("protection_metadata", result)
        self.assertIn("ciphertext_hash", result)
        self.assertIn("wrapped_key", result["protection_metadata"])

        recovered = unprotect_file_bytes(result["ciphertext"], result["protection_metadata"])
        self.assertEqual(recovered, plaintext)

    def test_each_file_gets_a_different_key(self):
        result1 = protect_file_bytes(b"file one")
        result2 = protect_file_bytes(b"file two")
        self.assertNotEqual(
            result1["protection_metadata"]["wrapped_key"],
            result2["protection_metadata"]["wrapped_key"],
        )

    def test_corrupted_stored_ciphertext_fails_integrity_check_and_decryption(self):
        plaintext = b"a file that will get corrupted in storage"
        result = protect_file_bytes(plaintext)

        # Flip a byte in the middle of the token (not the end -- a trailing
        # byte can be silently dropped by base64 decoding without altering
        # the underlying ciphertext/HMAC, which would defeat this test).
        ciphertext = bytearray(result["ciphertext"])
        mid = len(ciphertext) // 2
        ciphertext[mid] ^= 0xFF
        corrupted_ciphertext = bytes(ciphertext)

        # Integrity check (what an IntegrityRecord verification would do)
        self.assertFalse(verify_integrity(corrupted_ciphertext, result["ciphertext_hash"]))

        # Decryption should also fail outright, independent of the hash check
        with self.assertRaises(DecryptionError):
            unprotect_file_bytes(corrupted_ciphertext, result["protection_metadata"])

    def test_uncorrupted_data_passes_integrity_check(self):
        result = protect_file_bytes(b"clean data")
        self.assertTrue(verify_integrity(result["ciphertext"], result["ciphertext_hash"]))


class MasterKeyEncryptionTests(TestCase):
    def test_round_trip_and_ciphertext_hides_the_secret(self):
        token = encrypt_with_master_key(b"my-cloud-api-secret")
        self.assertNotIn("my-cloud-api-secret", token)
        self.assertEqual(decrypt_with_master_key(token), b"my-cloud-api-secret")

    def test_garbage_raises_decryption_error(self):
        with self.assertRaises(DecryptionError):
            decrypt_with_master_key("not-a-real-token")
