"""Timestamps in the machine's local timezone.

Persisted timestamps are offset-aware ISO strings. Compare them through
`timestamp_key`, never as plain strings: offsets differ across DST changes,
timezone moves and older UTC records.
"""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat()


def timestamp_key(value: str) -> datetime:
    """Sortable instant for a stored ISO timestamp; naive means UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
