"""Atomic file reads and writes that tolerate transient Windows sharing errors."""


def test_read_waits_out_a_writer_replacing_the_file(tmp_path, monkeypatch):
    """Windows raises a sharing error while another process replaces the file."""
    from pathlib import Path

    from meow.common import atomic

    target = tmp_path / "run.json"
    target.write_text("{}", encoding="utf-8")
    real = Path.read_text
    calls = []

    def flaky(self, *args, **kwargs):
        calls.append(self)
        if len(calls) == 1:
            raise PermissionError(13, "sharing violation")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(atomic.time, "sleep", lambda _seconds: None)
    assert atomic.read_text_with_retry(target) == "{}"
    assert len(calls) == len(["failed", "succeeded"])


def test_read_does_not_retry_a_missing_file(tmp_path):
    import pytest

    from meow.common import atomic

    with pytest.raises(FileNotFoundError):
        atomic.read_text_with_retry(tmp_path / "absent.json")
