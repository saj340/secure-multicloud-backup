from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ActivityLog

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Kept in the database as an audit trail, but left out of the user's feed: one per file
# would bury everything else after a large backup. The run summary and the completed or
# failed entries say the same thing once.
HIDDEN_ACTIONS = ["file_version_stored", "recovery_started"]


class ActivityListView(APIView):
    """The signed-in user's own activity, newest first. Read-only."""

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
        except ValueError:
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        # Metadata (email and IP of failed sign-ins) is left out on purpose: the screen
        # needs only what happened and when, and less data means less to leak.
        # Entries made in the same clock tick tie on time, so the id breaks the tie.
        entries = (
            ActivityLog.objects.filter(user=request.user)
            .exclude(action__in=HIDDEN_ACTIONS)
            .order_by("-created_at", "-id")[:limit]
        )
        return Response(
            [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "description": entry.description,
                    "level": entry.level,
                    "created_at": entry.created_at,
                }
                for entry in entries
            ]
        )
