from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .adapters.base import ProviderError
from .manager import (
    available_provider_types,
    check_provider,
    connect_provider,
    disconnect_provider,
)
from .models import CloudProvider
from .serializers import CloudProviderSerializer, ConnectProviderSerializer


def _own_provider(request, pk):
    # Filtering by user means another user's provider is a plain 404, not a 403.
    return get_object_or_404(CloudProvider, pk=pk, user=request.user)


class ProviderTypesView(APIView):
    def get(self, request):
        return Response(
            [
                {"value": value, "label": CloudProvider.ProviderType(value).label}
                for value in available_provider_types()
            ]
        )


class ProviderListView(APIView):
    def get(self, request):
        providers = request.user.cloud_providers.order_by("id")
        return Response(CloudProviderSerializer(providers, many=True).data)

    def post(self, request):
        serializer = ConnectProviderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            provider = connect_provider(request.user, **serializer.validated_data)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CloudProviderSerializer(provider).data, status=status.HTTP_201_CREATED)


class ProviderCheckView(APIView):
    def post(self, request, pk):
        provider = _own_provider(request, pk)
        check_provider(provider)
        return Response(CloudProviderSerializer(provider).data)


class ProviderDisconnectView(APIView):
    def post(self, request, pk):
        provider = _own_provider(request, pk)
        disconnect_provider(provider)
        return Response(CloudProviderSerializer(provider).data)
