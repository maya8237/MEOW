import time
from datetime import datetime

from meow.common.clock import now_iso, timestamp_key


def test_now_iso_uses_local_offset(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Jerusalem")
    time.tzset()
    try:
        stamp = datetime.fromisoformat(now_iso())
        assert stamp.utcoffset().total_seconds() in {7200, 10800}
    finally:
        monkeypatch.undo()
        time.tzset()


def test_timestamp_key_orders_mixed_offsets():
    stamps = ["2026-01-01T12:00:00+02:00", "2026-01-01T11:00:00+00:00"]
    assert max(stamps) == stamps[0]  # plain string order is wrong
    assert max(stamps, key=timestamp_key) == stamps[1]
    assert timestamp_key("2026-01-01T10:00:00") < timestamp_key(stamps[1])
