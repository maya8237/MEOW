"""The managed-process contract: exit codes, stderr lines, `status --json`."""

import asyncio
import json
import subprocess
import time

import pytest

from meow.cli.cli import cli_main
from meow.cli.run_contract import (
    EXIT_INTERRUPTED,
    EXIT_PR_FAILED,
    EXIT_TEMPFAIL,
    outcome_exit,
)
from meow.execution import sprint_runner
from meow.execution.run_failures import BlockingRunError
from meow.execution.run_state import RunStore

MANAGED_RESUMES = 6  # the [run] default
BEAT_AGE = 10
SLACK = 50


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    for key, value in (("user.name", "T"), ("user.email", "t@example.com")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".meow/\n.worktrees/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


def _run(tmp_path, monkeypatch, capsys, error):
    repo = _repo(tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)

    async def crash(*_args, **_kwargs):
        await asyncio.sleep(0)
        raise error

    monkeypatch.setattr(sprint_runner, "_drive_sprint", crash)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["run", "Add a thing", "-n", "--work-dir", str(repo)])
    return repo, exit_info.value.code, capsys.readouterr().err


def _line(stderr, prefix):
    lines = [line for line in stderr.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, stderr
    return json.loads(lines[0][len(prefix) :])


def test_run_parked_exits_75(tmp_path, monkeypatch, capsys):
    repo, code, err = _run(tmp_path, monkeypatch, capsys, RuntimeError("boom"))
    assert code == EXIT_TEMPFAIL
    payload = _line(err, "MEOW_RETRY ")
    record = RunStore(repo).load(payload["run_id"])
    assert record.phase == "parked"
    assert payload["next_command"].startswith(f"meow resume {record.id} --continue")
    assert "retry_after" in payload


def test_blocking_exits_1(tmp_path, monkeypatch, capsys):
    error = BlockingRunError("no such ref", cause="invalid_ref")
    repo, code, err = _run(tmp_path, monkeypatch, capsys, error)
    assert code == 1
    payload = _line(err, "MEOW_BLOCKED ")
    assert payload["cause"] == "invalid_ref"
    assert "no such ref" in payload["message"]
    assert payload["stop"] == "blocking"
    assert RunStore(repo).load(payload["run_id"]).phase == "failed"
    assert "MEOW_RETRY" not in err


def test_interrupt_exits_130(tmp_path, monkeypatch, capsys):
    code = _run(tmp_path, monkeypatch, capsys, KeyboardInterrupt())[1]
    assert code == EXIT_INTERRUPTED


def _parked(tmp_path):
    repo = _repo(tmp_path)
    store = RunStore(repo)
    record = store.create(
        source="prompt", request="x", repo=repo, worktree=repo, branch="dev"
    )
    store.transition(record.id, "generator_started")
    return repo, store, store.park(record.id, "boom", "transient", "error")


def _status_json(repo, run_id, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["status", run_id, "--json", "--work-dir", str(repo)])
    assert exit_info.value.code == 0
    return json.loads(capsys.readouterr().out)


def test_status_json_fields(tmp_path, capsys):
    repo, _store, record = _parked(tmp_path)
    payload = _status_json(repo, record.id, capsys)
    assert {
        "id",
        "phase",
        "settled",
        "retryable",
        "last_failure",
        "cause",
        "next_command",
        "attempts",
        "budget_left",
        "worker",
        "intent",
        "heartbeat_age",
    } <= set(payload)
    assert payload["phase"] == "parked"
    assert payload["settled"] is True
    assert payload["retryable"] is True
    assert payload["cause"] == "error"
    assert payload["attempts"] == 1
    assert payload["budget_left"] == MANAGED_RESUMES
    assert payload["next_command"].startswith(f"meow resume {record.id}")
    assert payload["worker"] == "not_background"
    assert "not_resumed_reason" not in payload


def test_status_json_reports_why_not_resumed(tmp_path, capsys):
    repo, store, record = _parked(tmp_path)
    store.transition(record.id, "parked", park={**record.park, "manager_resumes": 6})
    payload = _status_json(repo, record.id, capsys)
    assert payload["retryable"] is False
    assert payload["next_command"] is None
    assert payload["not_resumed_reason"] == "managed_resumes reached"
    assert payload["budget_left"] == 0


def test_status_json_blocking_failure_is_not_retryable(tmp_path, capsys):
    repo = _repo(tmp_path)
    store = RunStore(repo)
    record = store.create(
        source="prompt", request="x", repo=repo, worktree=repo, branch="dev"
    )
    sprint_runner._settle_failure(
        store, record.id, BlockingRunError("bad", cause="auth")
    )
    payload = _status_json(repo, record.id, capsys)
    assert (payload["phase"], payload["retryable"], payload["cause"]) == (
        "failed",
        False,
        "auth",
    )
    assert payload["not_resumed_reason"] == "blocking: auth"


def test_status_json_heartbeat_and_intent(tmp_path, capsys):
    repo = _repo(tmp_path)
    store = RunStore(repo)
    record = store.create(
        source="native", request="x", repo=repo, worktree=repo, branch="dev"
    )
    store.update_results(record.id, intent="plan", heartbeat_at=time.time() - BEAT_AGE)
    store.transition(record.id, "planned")
    payload = _status_json(repo, record.id, capsys)
    assert payload["intent"] == "plan"
    assert BEAT_AGE <= payload["heartbeat_age"] < BEAT_AGE + SLACK
    assert payload["settled"] is False


def test_pushed_but_pr_failed_exits_3(tmp_path):
    repo = _repo(tmp_path)
    store = RunStore(repo)
    record = store.create(
        source="prompt", request="x", repo=repo, worktree=repo, branch="dev"
    )
    store.transition(
        record.id,
        "delivered",
        delivery={"pushed": True, "pr": None, "pr_error": "token expired"},
    )
    assert outcome_exit(repo, record.id, None, ["run", "x"]) == EXIT_PR_FAILED
