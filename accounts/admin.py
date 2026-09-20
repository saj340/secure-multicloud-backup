from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import RateLimitCounter, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    list_display = ["email", "full_name", "is_staff", "is_active", "date_joined"]
    search_fields = ["email", "full_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "password1", "password2"),
        }),
    )
    readonly_fields = ["date_joined"]


@admin.register(RateLimitCounter)
class RateLimitCounterAdmin(admin.ModelAdmin):
    """Read-only: shows who is being throttled (identifiers are hashed)."""

    list_display = ["scope", "count", "window_started"]
    list_filter = ["scope"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
