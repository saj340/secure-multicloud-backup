from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from monitoring.models import ActivityLog
from monitoring.service import log_activity

from .models import BackupSet
from .serializers import BackupSetSerializer, RecordScheduleRunSerializer


def _own_set(request, pk):
    # Filtering by user means another user's set is a plain 404, not a 403.
    return get_object_or_404(BackupSet, pk=pk, user=request.user)


def _serializer(request, *args, **kwargs):
    return BackupSetSerializer(*args, context={"request": request}, **kwargs)


class SetListView(APIView):
    def get(self, request):
        return Response(_serializer(request, request.user.backup_sets.all(), many=True).data)

    def post(self, request):
        serializer = _serializer(request, data=request.data)
        serializer.is_valid(raise_exception=True)
        backup_set = serializer.save(user=request.user)
        return Response(_serializer(request, backup_set).data, status=status.HTTP_201_CREATED)


class SetDetailView(APIView):
    def get(self, request, pk):
        return Response(_serializer(request, _own_set(request, pk)).data)

    def patch(self, request, pk):
        serializer = _serializer(request, _own_set(request, pk), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(_serializer(request, serializer.save()).data)

    def delete(self, request, pk):
        # Backups already made from the set are kept: they are separate, dated backups.
        _own_set(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SetRecordRunView(APIView):
    """The app reports how a "Back up now" went, so the set can show when it last ran."""

    def post(self, request, pk):
        backup_set = _own_set(request, pk)
        incoming = RecordScheduleRunSerializer(data=request.data)
        incoming.is_valid(raise_exception=True)
        outcome = incoming.validated_data["status"]
        message = incoming.validated_data.get("message", "")

        backup_set.last_run_at = timezone.now()
        backup_set.last_result = outcome
        backup_set.save(update_fields=["last_run_at", "last_result"])

        log_activity(
            request.user,
            f"backup_set_{outcome}",
            f"Backup set '{backup_set.name}' {outcome}" + (f": {message}" if message else "."),
            ActivityLog.Level.INFO if outcome == "completed" else ActivityLog.Level.ERROR,
            backup_set_id=backup_set.id,
        )
        return Response(_serializer(request, backup_set).data)
