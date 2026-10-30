"""Shared timestamp and backup operations for synchronization modules."""
from datetime import datetime, timezone
import json


def parse_gmt(value):
    """Interpret timezone-free Garmin GMT values as UTC; normalize offsets."""
    if not isinstance(value, str):
        raise ValueError('GMT timestamp must be a string.')
    start = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start.astimezone(timezone.utc)


def utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def write_backup(directory, day, record):
    """Write the before-state completely before a caller changes remote data."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    (directory / f'{day}-{stamp}.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return stamp
