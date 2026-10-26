import datetime

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone


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
    last_run_started_at = models.DateTimeField(null=True, blank=True)
    last_run_version_floor = models.PositiveBigIntegerField(
        default=0,
        help_text="The highest file-version id the job's files had when the latest run began. "
        "Only versions with a higher id belong to that run. (Ids only ever go up, unlike "
        "clock times, which can tie.)",
    )

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


class BackupSet(models.Model):
    """
    A named list of folders and files the user backs up together. The set remembers
    the *places*, not the files in them: "Back up now" looks inside them at that
    moment, so files added since last time are included. Each run is its own dated
    backup, exactly like a scheduled run.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_sets"
    )
    name = models.CharField(max_length=255)
    sources = models.JSONField(
        default=list, help_text="Folder and file paths on the user's computer."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_backup_set_name_per_user")
        ]

    def __str__(self):
        return self.name


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
    last_result = models.CharField(max_length=20, blank=True)
    run_at = models.TimeField(
        default=datetime.time(2, 0),
        help_text="Time of day in the user's own timezone (see utc_offset_minutes).",
    )
    weekday = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(6)],
        help_text="0 = Monday ... 6 = Sunday. Only used by weekly schedules.",
    )
    utc_offset_minutes = models.IntegerField(
        default=0,
        help_text="The user's UTC offset when the schedule was saved, so run_at means their local time.",
    )

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    def _period(self):
        weekly = self.frequency == self.Frequency.WEEKLY
        return datetime.timedelta(days=7 if weekly else 1)

    def latest_occurrence(self, now=None):
        """The most recent scheduled time that is not in the future."""
        tz = timezone.get_fixed_timezone(self.utc_offset_minutes)
        local_now = (now or timezone.now()).astimezone(tz)
        candidate = local_now.replace(
            hour=self.run_at.hour, minute=self.run_at.minute, second=0, microsecond=0
        )
        if self.frequency == self.Frequency.WEEKLY:
            candidate -= datetime.timedelta(days=(local_now.weekday() - (self.weekday or 0)) % 7)
        if candidate > local_now:
            candidate -= self._period()
        return candidate

    def next_occurrence(self, now=None):
        return self.latest_occurrence(now) + self._period()

    def is_due(self, now=None):
        """
        Due when a scheduled time has passed since the last run (or, for a new
        schedule, since it was created). Several missed times still mean one run.
        """
        if not self.is_active:
            return False
        baseline = self.last_run_at or self.created_at
        return baseline < self.latest_occurrence(now)


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
