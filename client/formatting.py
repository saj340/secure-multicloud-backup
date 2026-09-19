from datetime import datetime


def format_timestamp(value) -> str:
    """Turn a server timestamp (ISO 8601, UTC) into local time for display."""
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
