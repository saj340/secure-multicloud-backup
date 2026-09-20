from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from monitoring.models import ActivityLog
from monitoring.service import log_activity

from .models import BackupSchedule
from .serializers import BackupScheduleSerializer, RecordScheduleRunSerializer


def _own_schedule(request, pk):
    return get_object_or_404(BackupSchedule, pk=pk, user=request.user)


class ScheduleListView(APIView):
    def get(self, request):
        schedules = request.user.backup_schedules.all()
        return Response(BackupScheduleSerializer(schedules, many=True).data)

    def post(self, request):
        serializer = BackupScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        schedule = serializer.save(user=request.user)
        return Response(BackupScheduleSerializer(schedule).data, status=status.HTTP_201_CREATED)


class ScheduleDetailView(APIView):
    def get(self, request, pk):
        return Response(BackupScheduleSerializer(_own_schedule(request, pk)).data)

    def patch(self, request, pk):
        serializer = BackupScheduleSerializer(_own_schedule(request, pk), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(BackupScheduleSerializer(serializer.save()).data)

    def delete(self, request, pk):
        _own_schedule(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScheduleRecordRunView(APIView):
    """
    The app reports how a scheduled run went. Recording it, even a failed one, is
    what moves the schedule on to its next occurrence.
    """

    def post(self, request, pk):
        schedule = _own_schedule(request, pk)
        serializer = RecordScheduleRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        outcome = serializer.validated_data["status"]
        message = serializer.validated_data.get("message", "")

        schedule.last_run_at = timezone.now()
        schedule.last_result = outcome
        schedule.save(update_fields=["last_run_at", "last_result"])

        succeeded = outcome == "completed"
        log_activity(
            request.user,
            f"scheduled_backup_{outcome}",
            f"Scheduled backup '{schedule.name}' {outcome}" + (f": {message}" if message else "."),
            ActivityLog.Level.INFO if succeeded else ActivityLog.Level.ERROR,
            schedule_id=schedule.id,
        )
        return Response(BackupScheduleSerializer(schedule).data)
