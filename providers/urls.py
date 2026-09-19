from django.urls import path

from . import views

app_name = "providers"

urlpatterns = [
    path("", views.ProviderListView.as_view(), name="list"),
    path("types/", views.ProviderTypesView.as_view(), name="types"),
    path("<int:pk>/check/", views.ProviderCheckView.as_view(), name="check"),
    path("<int:pk>/disconnect/", views.ProviderDisconnectView.as_view(), name="disconnect"),
]
