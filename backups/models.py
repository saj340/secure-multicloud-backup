from django.conf import settings
from django.db import models


class BackupJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_jobs"
    )
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class File(models.Model):
    backup_job = models.ForeignKey(BackupJob, on_delete=models.CASCADE, related_name="files")
    original_path = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_path


class FileVersion(models.Model):
    class UploadStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        UPLOADING = "uploading", "Uploading"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    original_filename = models.CharField(max_length=255)
    original_size = models.PositiveBigIntegerField(help_text="Size in bytes")
    backup_time = models.DateTimeField(auto_now_add=True)
    protection_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Encryption metadata (algorithm, IV, etc.) — never the key itself.",
    )
    upload_status = models.CharField(
        max_length=20, choices=UploadStatus.choices, default=UploadStatus.PENDING
    )

    class Meta:
        unique_together = ["file", "version_number"]
        ordering = ["file", "version_number"]

    def __str__(self):
        return f"{self.original_filename} v{self.version_number}"


class BackupSchedule(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_schedules"
    )
    name = models.CharField(max_length=255)
    source_path = models.TextField()
    frequency = models.CharField(max_length=20, choices=Frequency.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"


class RecoveryOperation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recovery_operations"
    )
    file_version = models.ForeignKey(
        FileVersion, on_delete=models.CASCADE, related_name="recovery_operations"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"Recovery of {self.file_version} ({self.get_status_display()})"
