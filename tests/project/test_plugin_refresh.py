"""Updating MEOW refreshes Claude Code's cached copy of its plugin."""

import subprocess

from meow.project import claude_settings


def _fake_run(calls, code=0):
    def run(command, **kwargs):
        calls.append(command[1:])
        return subprocess.CompletedProcess(command, code, "", "boom\n" if code else "")

    return run


def test_refresh_updates_marketplace_then_plugin(monkeypatch):
    calls = []
    monkeypatch.setattr(claude_settings, "_claude_binary", lambda: "/bin/claude")
    monkeypatch.setattr(claude_settings.subprocess, "run", _fake_run(calls))
    assert claude_settings.refresh_installed_plugin("meow") is None
    assert calls == [
        ["plugin", "marketplace", "update", "meow"],
        ["plugin", "update", "meow@meow"],
    ]


def test_refresh_reports_what_failed(monkeypatch):
    monkeypatch.setattr(claude_settings, "_claude_binary", lambda: "/bin/claude")
    monkeypatch.setattr(claude_settings.subprocess, "run", _fake_run([], code=1))
    warning = claude_settings.refresh_installed_plugin("meow")
    assert "marketplace update meow" in warning and "boom" in warning


def test_refresh_without_claude_cli_says_so():
    assert "claude CLI not found" in claude_settings.refresh_installed_plugin("meow")
