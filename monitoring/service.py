from .models import ActivityLog


def log_activity(user, action, description="", level=ActivityLog.Level.INFO, **metadata):
    return ActivityLog.objects.create(
        user=user, action=action, description=description, level=level, metadata=metadata
    )
