"""A stale Claude Code copy of the MEOW plugin is noticed and refreshed."""

from meow.project import plugin_drift as drift


def _setup(monkeypatch, loaded, refresh=None, wanted="0.1.38"):
    calls = []
    monkeypatch.setattr(drift, "_running", lambda: wanted)
    monkeypatch.setattr(drift, "installed_plugin_version", lambda *a, **k: loaded)
    monkeypatch.setattr(
        drift, "refresh_installed_plugin", lambda *a, **k: calls.append(a) or refresh
    )
    return calls


def test_matching_versions_say_nothing(monkeypatch):
    calls = _setup(monkeypatch, "0.1.38")
    assert drift.plugin_drift(fix=True) == []
    assert calls == []


def test_unknown_version_says_nothing(monkeypatch):
    _setup(monkeypatch, None)
    assert drift.plugin_drift(fix=True) == []


def test_check_reports_without_refreshing(monkeypatch):
    calls = _setup(monkeypatch, "0.1.37")
    (line,) = drift.plugin_drift(fix=False)
    assert "0.1.37" in line and "0.1.38" in line and "meow sync" in line
    assert calls == []


def test_fix_refreshes_and_says_to_reload(monkeypatch):
    calls = _setup(monkeypatch, "0.1.37")
    (line,) = drift.plugin_drift(fix=True)
    assert calls and "/reload-plugins" in line


def test_failed_refresh_gives_manual_steps(monkeypatch):
    _setup(monkeypatch, "0.1.37", refresh="boom")
    (line,) = drift.plugin_drift(fix=True)
    assert "boom" in line and "/plugin update meow@meow" in line
