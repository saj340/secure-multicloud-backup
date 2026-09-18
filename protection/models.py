from django.db import models

from backups.models import FileVersion


class IntegrityRecord(models.Model):
    class Scope(models.TextChoices):
        WHOLE_FILE = "whole_file", "Whole file"
        FRAGMENT = "fragment", "Fragment"

    file_version = models.ForeignKey(
        FileVersion, on_delete=models.CASCADE, related_name="integrity_records"
    )
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.WHOLE_FILE)
    algorithm = models.CharField(max_length=50, default="SHA-256")
    hash_value = models.CharField(max_length=128)
    is_valid = models.BooleanField(null=True, help_text="Result of the most recent verification.")
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_scope_display()} hash for {self.file_version}"
