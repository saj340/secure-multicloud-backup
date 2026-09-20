"""
Limits on operations that can be guessed at: logging in, resetting a
password, changing it.

Each scope in settings.RATE_LIMITS allows N attempts per window. Once N are
recorded within a window, further attempts are refused until the window that
started with the first one has passed. Counting what an attempt means is up
to the caller (for logins only failures count; for reset requests every
request does).

The state lives in the database, not in memory, so a lockout survives a
server restart, is shared between server processes, and each test starts
with a clean slate. Identifiers (emails, IP addresses) are stored hashed.
"""

import hashlib
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import RateLimitCounter


def _key(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()


def retry_after(scope: str, identifier: str) -> int:
    """Seconds until this identifier may try again; 0 if it may try now."""
    limit, window = settings.RATE_LIMITS[scope]
    counter = RateLimitCounter.objects.filter(scope=scope, key=_key(identifier)).first()
    if counter is None or counter.count < limit:
        return 0
    remaining = counter.window_started + window - timezone.now()
    return max(0, math.ceil(remaining.total_seconds()))


def blocked_for(*checks) -> int:
    """The longest wait among several (scope, identifier) checks; 0 if none blocks."""
    return max((retry_after(scope, identifier) for scope, identifier in checks), default=0)


def record(scope: str, identifier: str) -> None:
    _, window = settings.RATE_LIMITS[scope]
    now = timezone.now()
    with transaction.atomic():
        counter, created = RateLimitCounter.objects.select_for_update().get_or_create(
            scope=scope, key=_key(identifier), defaults={"window_started": now}
        )
        if not created and now - counter.window_started >= window:
            counter.count = 0
            counter.window_started = now
        counter.count += 1
        counter.save()

    longest = max(window for _limit, window in settings.RATE_LIMITS.values())
    RateLimitCounter.objects.filter(window_started__lt=now - longest - timedelta(hours=1)).delete()


def reset(scope: str, identifier: str) -> None:
    RateLimitCounter.objects.filter(scope=scope, key=_key(identifier)).delete()
