"""The managed-process contract: exit codes, stderr lines, `status --json`."""

import asyncio
import json
import subprocess
import time
from pathlib import Path

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


def _line_or_none(stderr, prefix):
    return next(
        (
            json.loads(line[len(prefix) :])
            for line in stderr.splitlines()
            if line.startswith(prefix)
        ),
        None,
    )


def test_record_free_blocking_exits_1(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    missing = FileNotFoundError("No plan file found; pass --plan explicitly.")
    monkeypatch.setattr(
        "meow.cli.cli._dispatch", lambda *_a, **_k: (_ for _ in ()).throw(missing)
    )
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["run", "x", "-n", "--work-dir", str(repo)])
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    payload = _line(err, "MEOW_BLOCKED ")
    assert (payload["run_id"], payload["cause"]) == (None, "invalid_input")
    assert "MEOW_RETRY" not in err


def test_record_free_unrecognised_error_is_not_retried(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(
        "meow.cli.cli._dispatch",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("worktree add failed")),
    )
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["run", "x", "-n", "--work-dir", str(repo)])
    assert exit_info.value.code == 1
    assert _line(capsys.readouterr().err, "MEOW_BLOCKED ")["stop"] == "blocking"


def test_record_free_timeout_is_retried(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(
        "meow.cli.cli._dispatch",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("fetch timed out")),
    )
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["run", "x", "-n", "--work-dir", str(repo)])
    assert exit_info.value.code == EXIT_TEMPFAIL
    payload = _line(capsys.readouterr().err, "MEOW_RETRY ")
    assert payload["run_id"] is None
    assert payload["next_command"].startswith("meow run x")


def test_driver_busy_refusal_is_a_retry_not_a_block(tmp_path, capsys):
    from meow.execution.orchestration.store import DriverBusyError
    from meow.execution.run_failures import DRIVER_BUSY_RETRY_AFTER

    busy = DriverBusyError("Another driver already owns this orchestrator (cli)")
    argv = ["resume", "abc123def456", "--continue"]
    assert outcome_exit(tmp_path, "abc123def456", busy, argv) == EXIT_TEMPFAIL
    err = capsys.readouterr().err
    payload = _line(err, "MEOW_RETRY ")
    assert payload["cause"] == "driver_busy"
    assert payload["retry_after"] == DRIVER_BUSY_RETRY_AFTER
    assert "driver" in payload["message"]
    assert payload["next_command"].startswith("meow resume abc123def456")
    assert "MEOW_BLOCKED" not in err


def test_supervised_run_exit_follows_the_record(tmp_path, monkeypatch, capsys):
    repo, _store, record = _parked(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")

    def supervised(_repo, _argv, on_created=None):
        on_created(record.id)
        return 1  # the record, not the supervisor's code, decides

    monkeypatch.setattr("meow.cli.cli.run_supervised", supervised)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["run", "x", "-n", "--work-dir", str(repo)])
    assert exit_info.value.code == EXIT_TEMPFAIL
    assert _line(capsys.readouterr().err, "MEOW_RETRY ")["run_id"] == record.id


def test_resume_continue_maps_the_settled_record(tmp_path, monkeypatch, capsys):
    repo, store, record = _parked(tmp_path)

    async def resumed(*_args, **_kwargs):
        await asyncio.sleep(0)
        store.transition(record.id, "resuming")
        store.transition(
            record.id,
            "delivered",
            delivery={"pushed": True, "pr_error": "token expired"},
        )
        return 0

    monkeypatch.setattr("meow.cli.cli.resume", resumed)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["resume", record.id, "--continue", "--work-dir", str(repo)])
    assert exit_info.value.code == EXIT_PR_FAILED


def test_resume_continue_failure_parks_and_retries(tmp_path, monkeypatch, capsys):
    repo, store, record = _parked(tmp_path)

    async def resumed(*_args, **_kwargs):
        await asyncio.sleep(0)
        store.transition(record.id, "resuming")
        store.park(record.id, "boom again", "transient", "error")
        raise RuntimeError("boom again")

    monkeypatch.setattr("meow.cli.cli.resume", resumed)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["resume", record.id, "--continue", "--work-dir", str(repo)])
    assert exit_info.value.code == EXIT_TEMPFAIL
    assert _line(capsys.readouterr().err, "MEOW_RETRY ")["run_id"] == record.id


def test_record_exit_reports_budget_exhausted(tmp_path, capsys):
    from meow.cli.run_contract import record_exit

    _repo_dir, store, record = _parked(tmp_path)
    store.transition(record.id, "parked", park={**record.park, "total_resumes": 20})
    assert record_exit(store.load(record.id)) == 1
    payload = _line(capsys.readouterr().err, "MEOW_BLOCKED ")
    assert payload["stop"] == "budget_exhausted"
    assert "max_total_resumes reached" in payload["message"]


def test_plan_intent_park_is_not_resumable(tmp_path, capsys):
    from meow.cli.run_contract import record_exit

    repo = _repo(tmp_path)
    store = RunStore(repo)
    record = store.create(
        source="native", request="x", repo=repo, worktree=repo, branch="dev"
    )
    store.update_results(record.id, intent="plan")
    store.transition(record.id, "planning")
    parked = store.park(record.id, "crash", "transient", "native_reported")
    assert parked.results.get("next_command") is None
    assert record_exit(parked) == 1
    assert _line(capsys.readouterr().err, "MEOW_BLOCKED ")["stop"] == "not_resumable"


def test_status_json_without_runs_prints_json_error(tmp_path, capsys):
    repo = _repo(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["status", "--json", "--work-dir", str(repo)])
    assert exit_info.value.code == 1
    assert "error" in json.loads(capsys.readouterr().out)


def _flaky_push(monkeypatch):
    from meow.execution import delivery

    real_git = delivery._git

    def flaky_git(directory, *args, check=True):
        if args[:1] == ("push",):
            raise delivery.GitCommandError(
                "push -u origin dev", "fatal: Connection reset by peer"
            )
        return real_git(directory, *args, check=check)

    monkeypatch.setattr(delivery, "_git", flaky_git)
    monkeypatch.setattr(
        delivery, "_branch_and_remote", lambda _a, _f=None: ("dev", "origin")
    )

    async def deliver(working_dir, *_args, run_id, record_root=None, **_kwargs):
        await asyncio.sleep(0)
        store = RunStore(record_root or working_dir)
        (Path(working_dir) / "new.txt").write_text("x", encoding="utf-8")
        store.transition(run_id, "checks_finished")
        delivery.deliver_verified_run(store, run_id, commit=True)

    monkeypatch.setattr(sprint_runner, "_drive_sprint", deliver)


def test_transient_push_failure_parks_and_retries(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    _flaky_push(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["run", "Add", "-np", "--work-dir", str(repo)])
    err = capsys.readouterr().err
    assert exit_info.value.code == EXIT_TEMPFAIL, err
    record = RunStore(repo).load(_line(err, "MEOW_RETRY ")["run_id"])
    assert record.phase == "parked"
    assert record.park["phase_before"] == "committed"
    assert _line_or_none(err, "MEOW_BLOCKED ") is None
