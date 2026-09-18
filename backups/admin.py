from django.contrib import admin

from .models import BackupJob, BackupSchedule, File, FileVersion, RecoveryOperation


@admin.register(BackupJob)
class BackupJobAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "status", "created_at"]
    list_filter = ["status"]


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ["original_path", "backup_job", "created_at"]


@admin.register(FileVersion)
class FileVersionAdmin(admin.ModelAdmin):
    list_display = ["original_filename", "version_number", "file", "upload_status", "backup_time"]
    list_filter = ["upload_status"]


@admin.register(BackupSchedule)
class BackupScheduleAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "frequency", "is_active", "last_run_at"]
    list_filter = ["frequency", "is_active"]


@admin.register(RecoveryOperation)
class RecoveryOperationAdmin(admin.ModelAdmin):
    list_display = ["file_version", "user", "status", "started_at", "completed_at"]
    list_filter = ["status"]
