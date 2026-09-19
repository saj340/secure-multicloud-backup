from django.contrib import admin

from .models import CloudProvider, StorageLocation


@admin.register(CloudProvider)
class CloudProviderAdmin(admin.ModelAdmin):
    list_display = ["display_name", "user", "provider_type", "status", "last_checked_at"]
    list_filter = ["provider_type", "status"]
    readonly_fields = ["encrypted_credentials"]


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin):
    list_display = ["file_version", "cloud_provider", "upload_status", "uploaded_at"]
    list_filter = ["upload_status"]
