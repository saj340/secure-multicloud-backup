"""
Storage adapter for S3 and S3-compatible services (Amazon S3, Backblaze B2,
Wasabi, Cloudflare R2, MinIO, ...), through boto3.

All the boto3 specifics (client setup, error codes) stay in here; callers
only see the StorageAdapter interface and its errors.
"""

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .base import ObjectNotFoundError, ProviderUnavailableError, StorageAdapter

NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


class S3Adapter(StorageAdapter):
    def __init__(
        self,
        bucket,
        access_key_id,
        secret_access_key,
        region=None,
        endpoint_url=None,
        max_attempts=3,
    ):
        for name, value in (
            ("bucket", bucket),
            ("access_key_id", access_key_id),
            ("secret_access_key", secret_access_key),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(name)

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region or "us-east-1",
            endpoint_url=endpoint_url or None,
            config=Config(
                retries={"max_attempts": max_attempts, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
                # Custom endpoints (B2, MinIO, ...) all accept path-style addressing.
                s3={"addressing_style": "path"} if endpoint_url else {},
                # Recent boto3 adds checksum headers and streaming trailers to every
                # request. Amazon accepts them, but several S3-compatible services
                # (Storj, for one) reject uploads with "MissingContentLength". Only
                # send them when an operation requires it.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def upload(self, remote_path: str, data: bytes) -> None:
        key = self._key(remote_path)
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
        except (ClientError, BotoCoreError) as exc:
            raise ProviderUnavailableError(f"Could not upload to {self.bucket}: {exc}") from exc

    def download(self, remote_path: str) -> bytes:
        key = self._key(remote_path)
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if _error_code(exc) in NOT_FOUND_CODES:
                raise ObjectNotFoundError(f"No object at {remote_path!r}") from exc
            raise ProviderUnavailableError(
                f"Could not download from {self.bucket}: {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise ProviderUnavailableError(f"Could not download from {self.bucket}: {exc}") from exc

    def delete(self, remote_path: str) -> None:
        key = self._key(remote_path)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)  # S3 deletes are idempotent
        except (ClientError, BotoCoreError) as exc:
            raise ProviderUnavailableError(f"Could not delete from {self.bucket}: {exc}") from exc

    def exists(self, remote_path: str) -> bool:
        key = self._key(remote_path)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if _error_code(exc) in NOT_FOUND_CODES:
                return False
            raise ProviderUnavailableError(f"Could not check {self.bucket}: {exc}") from exc
        except BotoCoreError as exc:
            raise ProviderUnavailableError(f"Could not check {self.bucket}: {exc}") from exc

    def health_check(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except (ClientError, BotoCoreError):
            return False

    @staticmethod
    def _key(remote_path: str) -> str:
        if not remote_path or remote_path.startswith("/"):
            raise ValueError(f"Invalid remote path: {remote_path!r}")
        return remote_path
