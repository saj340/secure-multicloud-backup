from rest_framework import serializers

from .manager import available_provider_types, storage_used
from .models import CloudProvider


class CloudProviderSerializer(serializers.ModelSerializer):
    provider_type_label = serializers.CharField(source="get_provider_type_display", read_only=True)
    storage_used_bytes = serializers.SerializerMethodField()

    class Meta:
        model = CloudProvider
        fields = [
            "id",
            "provider_type",
            "provider_type_label",
            "display_name",
            "status",
            "created_at",
            "last_checked_at",
            "storage_used_bytes",
        ]
        read_only_fields = fields

    def get_storage_used_bytes(self, provider):
        # The list view loads every provider's total in one query; a single provider
        # (just connected, checked, ...) has no such figure yet, so work it out.
        total = getattr(provider, "storage_used_bytes", None)
        return storage_used(provider) if total is None else total


class ConnectProviderSerializer(serializers.Serializer):
    provider_type = serializers.CharField()
    display_name = serializers.CharField(max_length=255)
    credentials = serializers.DictField(write_only=True)

    def validate_provider_type(self, value):
        allowed = available_provider_types()
        if value not in allowed:
            raise serializers.ValidationError(
                f"Unsupported provider type. Available: {', '.join(allowed)}."
            )
        return value


class ReconnectProviderSerializer(serializers.Serializer):
    # The type and name stay as they were: the fragments already stored are tied to this record.
    credentials = serializers.DictField(write_only=True)
