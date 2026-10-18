"""
Tests for the S3 adapter against moto's server, which speaks the real S3
protocol over real HTTP on a local port. No cloud account is involved.
"""

import logging
import os
import socket
import time
import uuid

import boto3
from django.test import SimpleTestCase, TestCase, override_settings
from moto.server import ThreadedMotoServer
from rest_framework.test import APIClient

from accounts.models import User
from backups.manager import (
    create_backup_job,
    recover_ciphertext,
    recovery_data_key,
    start_file_version,
    start_recovery,
    store_ciphertext,
)
from protection.crypto import decrypt_bytes, encrypt_bytes

from .adapters.base import (
    ObjectNotFoundError,
    ProviderConfigurationError,
    ProviderUnavailableError,
    StorageAdapter,
)
from .adapters.s3 import S3Adapter
from .manager import _build_adapter, connect_provider, get_adapter
from .models import CloudProvider

S3_COMPATIBLE = CloudProvider.ProviderType.S3_COMPATIBLE
SECRET = "TopSecret-S3-Key-98765"


def wait_for_port(port, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("The local S3 test server did not start.")


class MotoServerMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        logging.getLogger("werkzeug").setLevel(logging.ERROR)  # one log line per request otherwise
        cls.moto = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
        cls.moto.start()
        cls.addClassCleanup(cls.moto.stop)
        wait_for_port(port)

        cls.endpoint = f"http://127.0.0.1:{port}"
        cls.admin = boto3.client(
            "s3",
            endpoint_url=cls.endpoint,
            aws_access_key_id="test",
            aws_secret_access_key="test",
            region_name="us-east-1",
        )

    def new_bucket(self):
        name = f"bucket-{uuid.uuid4().hex[:12]}"
        self.admin.create_bucket(Bucket=name)
        return name

    def object_keys(self, bucket):
        listing = self.admin.list_objects_v2(Bucket=bucket)
        return [item["Key"] for item in listing.get("Contents", [])]

    def delete_bucket(self, bucket):
        for key in self.object_keys(bucket):
            self.admin.delete_object(Bucket=bucket, Key=key)
        self.admin.delete_bucket(Bucket=bucket)

    def credentials(self, bucket, **overrides):
        return {
            "endpoint_url": self.endpoint,
            "bucket": bucket,
            "access_key_id": "test",
            "secret_access_key": SECRET,
            **overrides,
        }


class S3AdapterTests(MotoServerMixin, SimpleTestCase):
    def setUp(self):
        self.bucket = self.new_bucket()
        self.adapter = S3Adapter(self.bucket, "test", "test", endpoint_url=self.endpoint)

    def test_is_a_storage_adapter(self):
        self.assertIsInstance(self.adapter, StorageAdapter)

    def test_checksums_are_only_sent_when_an_operation_requires_them(self):
        # Storj rejected uploads ("MissingContentLength") while boto3 added checksum
        # headers to every request. The fake server can't reproduce that, so this
        # pins the setting that fixed it against real services.
        config = self.adapter._client.meta.config
        self.assertEqual(config.request_checksum_calculation, "when_required")
        self.assertEqual(config.response_checksum_validation, "when_required")

    def test_upload_then_download_returns_identical_bytes(self):
        data = os.urandom(100_000)
        self.adapter.upload("u1/job1/file1/v1/part0.bin", data)
        self.assertEqual(self.adapter.download("u1/job1/file1/v1/part0.bin"), data)
        self.assertEqual(self.object_keys(self.bucket), ["u1/job1/file1/v1/part0.bin"])

    def test_upload_overwrites_an_existing_object(self):
        self.adapter.upload("a.bin", b"first")
        self.adapter.upload("a.bin", b"second")
        self.assertEqual(self.adapter.download("a.bin"), b"second")

    def test_exists_and_delete(self):
        self.assertFalse(self.adapter.exists("a.bin"))
        self.adapter.upload("a.bin", b"data")
        self.assertTrue(self.adapter.exists("a.bin"))

        self.adapter.delete("a.bin")
        self.assertFalse(self.adapter.exists("a.bin"))
        self.adapter.delete("a.bin")  # deleting something absent is not an error

    def test_downloading_a_missing_object_raises_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            self.adapter.download("nope.bin")

    def test_invalid_remote_paths_are_rejected(self):
        for bad_path in ("", "/absolute.bin"):
            with self.subTest(path=bad_path):
                with self.assertRaises(ValueError):
                    self.adapter.upload(bad_path, b"x")

    def test_blank_credentials_are_rejected_up_front(self):
        for args in (("", "k", "s"), (self.bucket, "", "s"), (self.bucket, "k", "")):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    S3Adapter(*args, endpoint_url=self.endpoint)

    def test_health_check_is_true_for_a_reachable_bucket(self):
        self.assertTrue(self.adapter.health_check())

    def test_a_missing_bucket_is_unhealthy_and_operations_report_the_provider_as_unavailable(self):
        adapter = S3Adapter("no-such-bucket-here", "test", "test", endpoint_url=self.endpoint)
        self.assertFalse(adapter.health_check())
        with self.assertRaises(ProviderUnavailableError):
            adapter.upload("a.bin", b"x")
        with self.assertRaises(ProviderUnavailableError):
            adapter.download("a.bin")  # a missing bucket is not a missing object

    def test_an_unreachable_endpoint_is_unhealthy_and_reports_unavailable(self):
        adapter = S3Adapter(
            self.bucket, "test", "test", endpoint_url="http://127.0.0.1:1", max_attempts=1
        )
        self.assertFalse(adapter.health_check())
        with self.assertRaises(ProviderUnavailableError):
            adapter.upload("a.bin", b"x")


@override_settings(ALLOW_PRIVATE_S3_ENDPOINTS=True)
class S3ManagerTests(MotoServerMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="s3@example.com", password="irrelevant-pass-1")

    def test_connect_stores_encrypted_credentials_and_builds_a_working_adapter(self):
        bucket = self.new_bucket()
        provider = connect_provider(self.user, S3_COMPATIBLE, "my b2", self.credentials(bucket))

        self.assertEqual(provider.status, CloudProvider.Status.CONNECTED)
        stored = CloudProvider.objects.get(pk=provider.pk).encrypted_credentials
        for secret in (SECRET, bucket, self.endpoint):
            self.assertNotIn(secret, stored)

        adapter = get_adapter(provider)
        self.assertIsInstance(adapter, S3Adapter)
        adapter.upload("a.bin", b"data")
        self.assertEqual(adapter.download("a.bin"), b"data")

    def test_a_missing_bucket_is_rejected_and_nothing_is_saved(self):
        with self.assertRaises(ProviderUnavailableError):
            connect_provider(self.user, S3_COMPATIBLE, "bad", self.credentials("no-such-bucket"))
        self.assertEqual(CloudProvider.objects.count(), 0)

    def test_missing_or_blank_credentials_are_a_configuration_error(self):
        bucket = self.new_bucket()
        incomplete = self.credentials(bucket)
        del incomplete["secret_access_key"]
        with self.assertRaises(ProviderConfigurationError):
            connect_provider(self.user, S3_COMPATIBLE, "bad", incomplete)
        with self.assertRaises(ProviderConfigurationError):
            connect_provider(self.user, S3_COMPATIBLE, "bad", {**self.credentials(bucket), "bucket": ""})
        self.assertEqual(CloudProvider.objects.count(), 0)

    def test_aws_needs_a_region_and_can_never_be_pointed_at_another_endpoint(self):
        credentials = {
            "bucket": "b",
            "access_key_id": "a",
            "secret_access_key": "s",
            "endpoint_url": "http://evil.example",
        }
        with self.assertRaises(ProviderConfigurationError):
            _build_adapter("aws_s3", credentials)  # no region

        adapter = _build_adapter("aws_s3", {**credentials, "region": "eu-west-1"})
        self.assertEqual(adapter._client.meta.endpoint_url, "https://s3.eu-west-1.amazonaws.com")

    @override_settings(ALLOW_PRIVATE_S3_ENDPOINTS=False)
    def test_outside_development_a_private_or_plain_http_endpoint_is_refused(self):
        bucket = self.new_bucket()
        with self.assertRaises(ProviderConfigurationError):
            connect_provider(self.user, S3_COMPATIBLE, "ssrf", self.credentials(bucket))
        self.assertEqual(CloudProvider.objects.count(), 0)

    def test_backup_and_recovery_across_three_s3_providers_survives_losing_one(self):
        buckets = [self.new_bucket() for _ in range(3)]
        for index, bucket in enumerate(buckets):
            connect_provider(self.user, S3_COMPATIBLE, f"s3-{index}", self.credentials(bucket))

        job = create_backup_job(self.user, "docs", ["C:/docs/report.txt"])
        plaintext = os.urandom(50_000)
        version, data_key = start_file_version(job.files.get(), "report.txt", len(plaintext))
        store_ciphertext(version, encrypt_bytes(plaintext, data_key))

        for bucket in buckets:
            self.assertEqual(len(self.object_keys(bucket)), 1)

        self.delete_bucket(buckets[1])  # one provider disappears entirely

        operation = start_recovery(self.user, version)
        ciphertext = recover_ciphertext(operation)
        recovered = decrypt_bytes(ciphertext, recovery_data_key(operation))
        self.assertEqual(recovered, plaintext)


@override_settings(ALLOW_PRIVATE_S3_ENDPOINTS=True)
class S3ApiTests(MotoServerMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="api@example.com", password="irrelevant-pass-1")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def connect(self, credentials, provider_type="s3_compatible"):
        return self.client.post(
            "/api/providers/",
            {"provider_type": provider_type, "display_name": "cloud", "credentials": credentials},
            format="json",
        )

    def test_the_s3_types_are_offered(self):
        types = {t["value"] for t in self.client.get("/api/providers/types/").data}
        self.assertTrue({"aws_s3", "s3_compatible"} <= types)

    def test_connecting_never_returns_or_stores_the_secret_in_the_clear(self):
        bucket = self.new_bucket()
        response = self.connect(self.credentials(bucket))

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["status"], "connected")
        self.assertNotIn(SECRET, response.content.decode())
        self.assertNotIn(SECRET, CloudProvider.objects.get().encrypted_credentials)

    def test_a_bad_bucket_or_bad_credentials_is_a_400_and_saves_nothing(self):
        bad_bucket = self.connect(self.credentials("no-such-bucket"))
        self.assertEqual(bad_bucket.status_code, 400)

        incomplete = self.credentials(self.new_bucket())
        del incomplete["access_key_id"]
        self.assertEqual(self.connect(incomplete).status_code, 400)
        self.assertEqual(CloudProvider.objects.count(), 0)
