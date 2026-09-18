from django.contrib import admin

from .models import IntegrityRecord


@admin.register(IntegrityRecord)
class IntegrityRecordAdmin(admin.ModelAdmin):
    list_display = ["file_version", "scope", "algorithm", "is_valid", "verified_at"]
    list_filter = ["scope", "algorithm", "is_valid"]
