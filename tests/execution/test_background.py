"""Detached worker registration and safe liveness checks."""

import os
import sys
import time
from types import SimpleNamespace

import pytest

from meow.cli import cli as cli_core
from meow.cli.status_cli import render
from meow.execution.run_state import RunStore
from meow.infrastructure import background


def test_background_requires_unattended(tmp_path):
    with pytest.raises(background.BackgroundError):
        background.launch_background(tmp_path, ["run", "feature", "--name", "x"])


def test_background_cli_requires_unattended(tmp_path, monkeypatch):
    usage_error = 2
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "meow",
            "run",
            "feature",
            "--name",
            "x",
            "--background",
            "--work-dir",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_core.cli_main()
    assert exc.value.code == usage_error


def test_background_cli_returns_run_id_without_running_agent(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "meow",
            "run",
            "feature",
            "--name",
            "x",
            "--unattended",
            "--background",
            "--work-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(cli_core, "launch_background", lambda *_args: "run-123")
    cli_core.cli_main()
    assert "Background run: run-123" in capsys.readouterr().out


def test_launch_creates_checkpoint_and_worker_log(tmp_path, monkeypatch):
    worker_pid = 123
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append((argv, kwargs))
        return SimpleNamespace(pid=worker_pid)

    monkeypatch.setattr(background.subprocess, "Popen", fake_popen)
    run_id = background.launch_background(
        tmp_path, ["run", "feature", "--name", "x", "--unattended", "--background"]
    )
    record = RunStore(tmp_path).load(run_id)
    assert record.background["launcher_pid"] == worker_pid
    assert record.background["state"] == "starting"
    assert (RunStore(tmp_path).directory / f"{run_id}.log").is_file()
    assert spawned[0][0][:2] == [sys.executable, "-c"]
    assert spawned[0][1]["stdin"] == background.subprocess.DEVNULL


def test_real_worker_survives_launcher_and_reports_failure(tmp_path):
    # An absent MEOW config makes the fixture worker exit without an agent.
    run_id = background.launch_background(
        tmp_path,
        ["run", "feature", "--name", "x", "--unattended", "--background"],
    )
    store = RunStore(tmp_path)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if store.load(run_id).phase == "failed":
            break
        time.sleep(0.1)
    assert store.load(run_id).phase == "failed"
    assert (store.directory / f"{run_id}.log").is_file()


def test_stale_pid_reconciles_as_interrupted(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.update_background(
        record.id, pid=os.getpid(), start_identity="different", log="run.log"
    )
    monkeypatch.setattr(background, "_process_identity", lambda _pid: "real")
    assert background.reconcile_worker(store, record.id).state == "interrupted"
    assert store.load(record.id).phase == "interrupted_mutation"
    transition_count = len(store.load(record.id).transitions)
    background.reconcile_worker(store, record.id)
    assert len(store.load(record.id).transitions) == transition_count
    assert "Background worker: interrupted" in render(store.load(record.id))
    assert "Worker log: run.log" in render(store.load(record.id))


def test_status_labels_browser_with_no_result_unavailable(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(
        record.id,
        "checks_finished",
        results={
            "checks": [
                {
                    "kind": "browser",
                    "required": True,
                    "exit_code": None,
                    "timed_out": False,
                }
            ],
        },
    )
    assert "Browser: UNAVAILABLE" in render(store.load(record.id))


def test_worker_runs_saved_sprint_and_completes(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    identity = background._process_identity(os.getpid())
    store.update_background(
        record.id,
        launcher_pid=os.getpid(),
        nonce="n",
        argv=["run", "feature", "--name", "x", "--unattended", "--background"],
    )

    async def fake_run_sprint(*_args, **kwargs):  # ruff: ignore[unused-async]
        assert kwargs["run_id"] == record.id
        store.transition(record.id, "complete")

    monkeypatch.setattr("meow.execution.sprint_runner.run_sprint", fake_run_sprint)
    assert background.worker_main(tmp_path, record.id, "n") == 0
    assert store.load(record.id).phase == "complete"
    assert store.load(record.id).background["start_identity"] == identity


@pytest.mark.parametrize("terminal_phase", ["complete", "needs_user_decision"])
def test_notification_is_opt_in_and_sent_once_with_summary_only(
    tmp_path, monkeypatch, terminal_phase
):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="private request",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(record.id, terminal_phase)
    (tmp_path / ".meow" / "config.toml").write_text(
        '[background]\nnotify_command = ["notify"]\n', encoding="utf-8"
    )
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(background.subprocess, "run", fake_run)
    background._notify_once(store, record.id)
    background._notify_once(store, record.id)
    assert len(calls) == 1
    assert calls[0] == ["notify", record.id, terminal_phase, f"meow status {record.id}"]
    assert store.load(record.id).background["notification"] == "sent"


def test_background_cli_launches_from_the_argv_it_was_given(tmp_path, monkeypatch):
    argv = [
        "run",
        "feature",
        "--name",
        "x",
        "--unattended",
        "--background",
        "--work-dir",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", ["ipython"])
    launched = []
    monkeypatch.setattr(
        cli_core,
        "launch_background",
        lambda _repo, saved: launched.append(saved) or "run-123",
    )
    cli_core.cli_main(argv)
    assert launched == [argv]
