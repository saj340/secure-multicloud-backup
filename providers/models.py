from django.conf import settings
from django.db import models

from backups.models import FileVersion


class CloudProvider(models.Model):
    class ProviderType(models.TextChoices):
        LOCAL_FOLDER = "local_folder", "Local folder"
        AWS_S3 = "aws_s3", "Amazon S3"
        S3_COMPATIBLE = "s3_compatible", "S3-compatible storage"

    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cloud_providers"
    )
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices)
    display_name = models.CharField(max_length=255)
    encrypted_credentials = models.TextField(
        blank=True,
        help_text="Ciphertext only (encrypted with the master key by providers.manager). "
        "Never store plaintext credentials here.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DISCONNECTED)
    created_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.display_name} ({self.get_provider_type_display()})"


class StorageLocation(models.Model):
    class UploadStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        UPLOADING = "uploading", "Uploading"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    file_version = models.ForeignKey(
        FileVersion, on_delete=models.CASCADE, related_name="storage_locations"
    )
    cloud_provider = models.ForeignKey(
        CloudProvider, on_delete=models.CASCADE, related_name="storage_locations"
    )
    fragment_index = models.PositiveSmallIntegerField(
        default=0, help_text="Which fragment of the file version this location holds."
    )
    remote_path = models.TextField()
    upload_status = models.CharField(
        max_length=20, choices=UploadStatus.choices, default=UploadStatus.PENDING
    )
    uploaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["file_version", "fragment_index"]
        ordering = ["file_version", "fragment_index"]

    def __str__(self):
        return f"{self.file_version} @ {self.cloud_provider.display_name}"
