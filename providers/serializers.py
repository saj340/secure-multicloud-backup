from rest_framework import serializers

from .manager import available_provider_types
from .models import CloudProvider


class CloudProviderSerializer(serializers.ModelSerializer):
    provider_type_label = serializers.CharField(source="get_provider_type_display", read_only=True)

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
        ]
        read_only_fields = fields


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
