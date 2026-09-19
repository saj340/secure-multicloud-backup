from rest_framework import serializers

from .models import BackupJob, File, FileVersion, RecoveryOperation


class FileVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileVersion
        fields = [
            "id",
            "version_number",
            "original_filename",
            "original_size",
            "backup_time",
            "upload_status",
        ]
        read_only_fields = fields


class FileSerializer(serializers.ModelSerializer):
    versions = FileVersionSerializer(many=True, read_only=True)

    class Meta:
        model = File
        fields = ["id", "original_path", "versions"]
        read_only_fields = fields


class BackupJobSerializer(serializers.ModelSerializer):
    file_count = serializers.IntegerField(source="files.count", read_only=True)

    class Meta:
        model = BackupJob
        fields = ["id", "name", "status", "created_at", "file_count"]
        read_only_fields = fields


class BackupJobDetailSerializer(BackupJobSerializer):
    files = FileSerializer(many=True, read_only=True)

    class Meta(BackupJobSerializer.Meta):
        fields = BackupJobSerializer.Meta.fields + ["files"]
        read_only_fields = fields


class CreateBackupJobSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    paths = serializers.ListField(child=serializers.CharField(), allow_empty=False)


class StartVersionSerializer(serializers.Serializer):
    original_filename = serializers.CharField(max_length=255)
    original_size = serializers.IntegerField(min_value=0)


class RecoveryOperationSerializer(serializers.ModelSerializer):
    original_filename = serializers.CharField(
        source="file_version.original_filename", read_only=True
    )
    version_number = serializers.IntegerField(source="file_version.version_number", read_only=True)

    class Meta:
        model = RecoveryOperation
        fields = [
            "id",
            "file_version",
            "original_filename",
            "version_number",
            "status",
            "started_at",
            "completed_at",
            "error_message",
        ]
        read_only_fields = fields


class StartRecoverySerializer(serializers.Serializer):
    version_id = serializers.IntegerField()


class FailRecoverySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=1000)
