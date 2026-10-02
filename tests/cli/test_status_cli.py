"""Read-only status reports for durable runs."""

import sys

from meow.cli import cli_main
from meow.run_state import RunStore
from meow.status_cli import status


def test_status_latest_and_verbose(tmp_path, capsys):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(
        record.id, "interrupted_mutation", round=2, last_failure="process stopped"
    )
    assert status(tmp_path) == 0
    concise = capsys.readouterr().out
    assert record.id in concise and "interrupted_mutation" in concise
    assert "meow resume" in concise and "dev" in concise
    assert status(tmp_path, record.id, verbose=True) == 0
    verbose = capsys.readouterr().out
    assert "Transitions" in verbose and "unavailable" in verbose


def test_status_missing_record_is_safe(tmp_path, capsys):
    assert status(tmp_path, "unknown") != 0
    assert "meow status" in capsys.readouterr().err


def test_cli_status_never_boots_repo(tmp_path, monkeypatch, capsys):
    record = RunStore(tmp_path).create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    monkeypatch.setattr(
        sys, "argv", ["meow", "status", record.id, "--working-dir", str(tmp_path)]
    )
    monkeypatch.setattr("meow.cli._boot_repo", lambda *args, **kwargs: 1 / 0)
    with __import__("pytest").raises(SystemExit) as exit_info:
        cli_main()
    assert exit_info.value.code == 0
    assert record.id in capsys.readouterr().out
