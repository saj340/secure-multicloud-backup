from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    action = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.INFO)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_level_display()}] {self.action}"
