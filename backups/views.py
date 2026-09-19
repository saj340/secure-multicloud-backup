from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import BaseParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .manager import (
    BackupError,
    NotEnoughProvidersError,
    RecoveryError,
    begin_backup_run,
    complete_recovery,
    create_backup_job,
    fail_recovery,
    finish_backup_run,
    recover_ciphertext,
    recovery_data_key,
    start_file_version,
    start_recovery,
    store_ciphertext,
)
from .models import BackupJob, File, FileVersion, RecoveryOperation
from .serializers import (
    BackupJobDetailSerializer,
    BackupJobSerializer,
    CreateBackupJobSerializer,
    FailRecoverySerializer,
    FileVersionSerializer,
    RecoveryOperationSerializer,
    StartRecoverySerializer,
    StartVersionSerializer,
)


class RawBytesParser(BaseParser):
    """Lets an endpoint accept a request body that is just raw bytes (the ciphertext)."""

    media_type = "application/octet-stream"

    def parse(self, stream, media_type=None, parser_context=None):
        return stream.read()


# Every lookup is filtered by the requesting user, so another user's objects
# are a plain 404 rather than a 403 that would confirm they exist.
def _own_job(request, pk):
    return get_object_or_404(BackupJob, pk=pk, user=request.user)


def _own_file(request, pk):
    return get_object_or_404(File, pk=pk, backup_job__user=request.user)


def _own_version(request, pk):
    return get_object_or_404(FileVersion, pk=pk, file__backup_job__user=request.user)


def _own_recovery(request, pk):
    return get_object_or_404(RecoveryOperation, pk=pk, user=request.user)


def _conflict(message):
    return Response({"detail": message}, status=status.HTTP_409_CONFLICT)


def _not_in_progress(operation):
    if operation.status != RecoveryOperation.Status.IN_PROGRESS:
        return _conflict("This recovery is not in progress.")
    return None


class JobListView(APIView):
    def get(self, request):
        jobs = BackupJob.objects.filter(user=request.user).order_by("-created_at")
        return Response(BackupJobSerializer(jobs, many=True).data)

    def post(self, request):
        serializer = CreateBackupJobSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = create_backup_job(request.user, **serializer.validated_data)
        return Response(BackupJobDetailSerializer(job).data, status=status.HTTP_201_CREATED)


class JobDetailView(APIView):
    def get(self, request, pk):
        return Response(BackupJobDetailSerializer(_own_job(request, pk)).data)


class JobBeginView(APIView):
    def post(self, request, pk):
        job = _own_job(request, pk)
        begin_backup_run(job)
        return Response(BackupJobSerializer(job).data)


class JobFinishView(APIView):
    def post(self, request, pk):
        job = finish_backup_run(_own_job(request, pk))
        return Response(BackupJobSerializer(job).data)


class FileVersionCreateView(APIView):
    def post(self, request, pk):
        file = _own_file(request, pk)
        serializer = StartVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version, data_key = start_file_version(file, **serializer.validated_data)
        return Response(
            {"version": FileVersionSerializer(version).data, "data_key": data_key.decode()},
            status=status.HTTP_201_CREATED,
        )


class CiphertextUploadView(APIView):
    parser_classes = [RawBytesParser]

    def put(self, request, pk):
        version = _own_version(request, pk)
        if version.upload_status != FileVersion.UploadStatus.PENDING:
            return _conflict("This version already has data. Start a new version to upload again.")

        # Checked before the body is read, so an oversized upload is refused cheaply.
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return Response({"detail": "Invalid Content-Length."}, status=status.HTTP_400_BAD_REQUEST)
        if length > settings.MAX_UPLOAD_BYTES:
            return Response({"detail": "Upload is too large."}, status=413)

        ciphertext = request.data
        if not isinstance(ciphertext, bytes) or not ciphertext:
            return Response(
                {"detail": "The request body must be the ciphertext bytes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            store_ciphertext(version, ciphertext)
        except NotEnoughProvidersError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except BackupError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        version.refresh_from_db()
        return Response(FileVersionSerializer(version).data)


class RecoveryListView(APIView):
    def get(self, request):
        operations = (
            RecoveryOperation.objects.filter(user=request.user)
            .select_related("file_version")
            .order_by("-started_at")
        )
        return Response(RecoveryOperationSerializer(operations, many=True).data)

    def post(self, request):
        serializer = StartRecoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = _own_version(request, serializer.validated_data["version_id"])
        try:
            operation = start_recovery(request.user, version)
        except RecoveryError as exc:
            return _conflict(str(exc))
        return Response(
            RecoveryOperationSerializer(operation).data, status=status.HTTP_201_CREATED
        )


class RecoveryCiphertextView(APIView):
    def get(self, request, pk):
        operation = _own_recovery(request, pk)
        if (blocked := _not_in_progress(operation)) is not None:
            return blocked
        try:
            ciphertext = recover_ciphertext(operation)
        except RecoveryError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return HttpResponse(ciphertext, content_type="application/octet-stream")


class RecoveryKeyView(APIView):
    def get(self, request, pk):
        operation = _own_recovery(request, pk)
        if (blocked := _not_in_progress(operation)) is not None:
            return blocked
        return Response({"data_key": recovery_data_key(operation).decode()})


class RecoveryCompleteView(APIView):
    def post(self, request, pk):
        operation = _own_recovery(request, pk)
        if (blocked := _not_in_progress(operation)) is not None:
            return blocked
        complete_recovery(operation)
        return Response(RecoveryOperationSerializer(operation).data)


class RecoveryFailView(APIView):
    def post(self, request, pk):
        operation = _own_recovery(request, pk)
        if (blocked := _not_in_progress(operation)) is not None:
            return blocked
        serializer = FailRecoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fail_recovery(operation, serializer.validated_data["message"])
        return Response(RecoveryOperationSerializer(operation).data)
