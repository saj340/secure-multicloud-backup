from django.urls import path

from . import schedule_views, views

app_name = "backups"

urlpatterns = [
    path("jobs/", views.JobListView.as_view(), name="job-list"),
    path("jobs/<int:pk>/", views.JobDetailView.as_view(), name="job-detail"),
    path("jobs/<int:pk>/begin/", views.JobBeginView.as_view(), name="job-begin"),
    path("jobs/<int:pk>/finish/", views.JobFinishView.as_view(), name="job-finish"),
    path("files/<int:pk>/versions/", views.FileVersionCreateView.as_view(), name="version-create"),
    path(
        "versions/<int:pk>/ciphertext/",
        views.CiphertextUploadView.as_view(),
        name="version-ciphertext",
    ),
    path("recoveries/", views.RecoveryListView.as_view(), name="recovery-list"),
    path(
        "recoveries/<int:pk>/ciphertext/",
        views.RecoveryCiphertextView.as_view(),
        name="recovery-ciphertext",
    ),
    path("recoveries/<int:pk>/key/", views.RecoveryKeyView.as_view(), name="recovery-key"),
    path(
        "recoveries/<int:pk>/complete/",
        views.RecoveryCompleteView.as_view(),
        name="recovery-complete",
    ),
    path("recoveries/<int:pk>/fail/", views.RecoveryFailView.as_view(), name="recovery-fail"),
    path("schedules/", schedule_views.ScheduleListView.as_view(), name="schedule-list"),
    path("schedules/<int:pk>/", schedule_views.ScheduleDetailView.as_view(), name="schedule-detail"),
    path(
        "schedules/<int:pk>/record-run/",
        schedule_views.ScheduleRecordRunView.as_view(),
        name="schedule-record-run",
    ),
]
