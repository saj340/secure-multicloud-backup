from rest_framework import serializers

from .models import BackupJob, BackupSchedule, BackupSet, File, FileVersion, RecoveryOperation

MAX_SET_SOURCES = 50
MAX_SOURCE_LENGTH = 1000


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


class BackupScheduleSerializer(serializers.ModelSerializer):
    run_at = serializers.TimeField(format="%H:%M", input_formats=["%H:%M", "%H:%M:%S"])
    is_due = serializers.SerializerMethodField()
    next_run_at = serializers.SerializerMethodField()

    class Meta:
        model = BackupSchedule
        fields = [
            "id",
            "name",
            "source_path",
            "frequency",
            "run_at",
            "weekday",
            "utc_offset_minutes",
            "is_active",
            "created_at",
            "last_run_at",
            "last_result",
            "is_due",
            "next_run_at",
        ]
        read_only_fields = ["id", "created_at", "last_run_at", "last_result", "is_due", "next_run_at"]

    def get_is_due(self, schedule):
        return schedule.is_due()

    def get_next_run_at(self, schedule):
        return schedule.next_occurrence() if schedule.is_active else None

    def validate_utc_offset_minutes(self, value):
        if not -14 * 60 <= value <= 14 * 60:
            raise serializers.ValidationError("That is not a real UTC offset.")
        return value

    def validate(self, data):
        frequency = data.get("frequency", self.instance.frequency if self.instance else None)
        weekday = data.get("weekday", self.instance.weekday if self.instance else None)
        if frequency == BackupSchedule.Frequency.WEEKLY and weekday is None:
            raise serializers.ValidationError({"weekday": "Choose a day of the week."})
        if frequency == BackupSchedule.Frequency.DAILY:
            data["weekday"] = None
        return data


class RecordScheduleRunSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["completed", "failed"])
    message = serializers.CharField(max_length=500, required=False, allow_blank=True)


class BackupSetSerializer(serializers.ModelSerializer):
    sources = serializers.ListField(
        child=serializers.CharField(max_length=MAX_SOURCE_LENGTH, allow_blank=True),
        max_length=MAX_SET_SOURCES,
    )

    class Meta:
        model = BackupSet
        fields = ["id", "name", "sources", "created_at", "last_run_at", "last_result"]
        read_only_fields = ["id", "created_at", "last_run_at", "last_result"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Give the set a name.")
        user = self.context["request"].user
        taken = BackupSet.objects.filter(user=user, name__iexact=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError("You already have a set with that name.")
        return value

    def validate_sources(self, value):
        # Blank entries are dropped and repeats collapsed, keeping the order they were given in.
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned:
            raise serializers.ValidationError("Add at least one folder or file.")
        return cleaned
