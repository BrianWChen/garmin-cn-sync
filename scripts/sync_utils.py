"""Shared timestamp handling for synchronization modules."""
from datetime import datetime, timezone


def parse_gmt(value):
    """Interpret timezone-free Garmin GMT values as UTC; normalize offsets."""
    if not isinstance(value, str):
        raise ValueError('GMT timestamp must be a string.')
    start = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start.astimezone(timezone.utc)
