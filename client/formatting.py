from datetime import datetime

# Backup jobs, file versions and recoveries share these statuses: (text, color).
STATUS_DISPLAY = {
    "completed": ("Completed", "#1b7f3b"),
    "failed": ("Failed", "#b00020"),
    "in_progress": ("In progress", "#b26a00"),
    "uploading": ("Uploading", "#b26a00"),
    "pending": ("Pending", "#6b6b6b"),
}


def format_timestamp(value) -> str:
    """Turn a server timestamp (ISO 8601, UTC) into local time for display."""
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def describe_status(status: str):
    return STATUS_DISPLAY.get(status, (status.replace("_", " ").capitalize(), "#000000"))
