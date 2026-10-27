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


def test_background_requires_background_flag(tmp_path):
    with pytest.raises(background.BackgroundError):
        background.launch_background(tmp_path, ["run", "feature", "--name", "x"])


@pytest.mark.parametrize("flag", ["-m", "--lint-fix"])
def test_background_cli_rejects_interactive_or_lint_modes(tmp_path, monkeypatch, flag):
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
            flag,
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
        tmp_path, ["run", "feature", "--name", "x", "--background"]
    )
    record = RunStore(tmp_path).load(run_id)
    assert record.background["launcher_pid"] == worker_pid
    assert record.background["state"] == "starting"
    assert (RunStore(tmp_path).directory / f"{run_id}.log").is_file()
    assert spawned[0][0][:3] == [sys.executable, "-u", "-c"]
    assert spawned[0][1]["stdin"] == background.subprocess.DEVNULL


@pytest.mark.parametrize(
    "flag,delivery",
    [
        ("-n", {"enabled": False, "push": False}),
        ("-nc", {"enabled": True, "push": False}),
        ("-np", {"enabled": True, "push": True}),
    ],
)
def test_delivery_choice_is_saved_before_worker_starts(
    tmp_path, monkeypatch, flag, delivery
):
    def spawn(store, repo, run_id, argv):
        assert store.load(run_id).delivery == delivery

    monkeypatch.setattr(background, "_spawn_worker", spawn)
    background.launch_background(tmp_path, ["run", "feature", "--background", flag])


def test_real_worker_survives_launcher_and_reports_failure(tmp_path):
    # An absent MEOW config makes the fixture worker exit without an agent.
    run_id = background.launch_background(
        tmp_path,
        ["run", "feature", "--name", "x", "--background"],
    )
    store = RunStore(tmp_path)
    # Cold Windows startup can exceed ten seconds on a busy machine.
    deadline = time.monotonic() + 30
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


def test_parked_run_with_dead_worker_stays_parked(tmp_path, monkeypatch):
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
    store.park(record.id, RuntimeError("boom"), "transient", "error")
    monkeypatch.setattr(background, "_process_identity", lambda _pid: "real")
    assert background.reconcile_worker(store, record.id).state == "parked"
    assert store.load(record.id).phase == "parked"


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


@pytest.mark.parametrize(
    "flag,delivery",
    [
        (None, {}),
        ("-n", {"commit": False, "push": False}),
        ("-nc", {"commit": True, "push": False}),
        ("-np", {"commit": True, "push": True}),
    ],
)
def test_worker_runs_saved_sprint_and_completes(tmp_path, monkeypatch, flag, delivery):
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
        argv=[
            "run",
            "feature",
            "--name",
            "x",
            "--background",
            *([flag] if flag else []),
        ],
    )

    async def fake_run_sprint(*_args, **kwargs):  # ruff: ignore[unused-async]
        assert kwargs["run_id"] == record.id
        assert kwargs["use_worktree"] is ("commit" not in delivery)
        assert {key: kwargs[key] for key in delivery} == delivery
        store.transition(record.id, "complete")

    monkeypatch.setattr("meow.execution.sprint_runner.run_sprint", fake_run_sprint)
    assert background.worker_main(tmp_path, record.id, "n") == 0
    assert store.load(record.id).phase == "complete"
    assert store.load(record.id).background["start_identity"] == identity


@pytest.mark.parametrize("terminal_phase", ["complete", "cancelled"])
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


ORCHESTRATOR_ID = "0123456789ab"


def test_orchestrator_task_launch_uses_seed(tmp_path, monkeypatch):
    worker_pid = 7
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        return SimpleNamespace(pid=worker_pid)

    monkeypatch.setattr(background.subprocess, "Popen", fake_popen)
    seed = background.RunSeed(
        "orchestrator", "do it", tmp_path / "wt", "orchestrator/abc-t1"
    )
    argv = ["_orchestrator-task", ORCHESTRATOR_ID, "t1", "--resume-at", "review"]

    run_id = background.launch_background(tmp_path, argv, seed=seed)

    record = RunStore(tmp_path).load(run_id)
    assert record.source == "orchestrator"
    assert record.request == "do it"
    assert record.branch == "orchestrator/abc-t1"
    assert record.worktree == str((tmp_path / "wt").resolve())
    assert record.background["argv"] == argv
    assert record.background["launcher_pid"] == worker_pid
    assert spawned[0][4:6] == ["_worker", run_id]


def test_orchestrator_task_launch_without_seed_is_rejected(tmp_path):
    with pytest.raises(background.BackgroundError, match="requires a run seed"):
        background.launch_background(
            tmp_path, ["_orchestrator-task", ORCHESTRATOR_ID, "t1"]
        )
    assert not RunStore(tmp_path).directory.exists()


def test_run_launch_with_a_seed_is_rejected(tmp_path):
    seed = background.RunSeed("orchestrator", "x", tmp_path, "b")
    argv = ["run", "feature", "--name", "x", "--background"]
    with pytest.raises(background.BackgroundError, match="does not take a run seed"):
        background.launch_background(tmp_path, argv, seed=seed)


def test_run_without_background_is_rejected(tmp_path):
    with pytest.raises(background.BackgroundError, match="run --background"):
        background.launch_background(tmp_path, ["run", "x", "--name", "y"])


def test_invocation_kind_classifies_saved_argv():
    from meow.cli.parser import _build_arg_parser

    parser = _build_arg_parser()
    run_args = parser.parse_args([
        "run",
        "feature",
        "--name",
        "x",
        "--background",
    ])
    task_args = parser.parse_args(["_orchestrator-task", ORCHESTRATOR_ID, "t1"])
    status_args = parser.parse_args(["status"])

    assert background._invocation_kind(run_args) == "run"
    assert background._invocation_kind(task_args) == "orchestrator-task"
    with pytest.raises(background.BackgroundError):
        background._invocation_kind(status_args)


def test_orchestrator_task_parser_options():
    from meow.cli.parser import _build_arg_parser

    parser = _build_arg_parser()
    defaults = parser.parse_args(["_orchestrator-task", ORCHESTRATOR_ID, "t1"])
    assert (defaults.orchestrator_id, defaults.task_id) == (ORCHESTRATOR_ID, "t1")
    assert defaults.resume_at == "generate"
    assert defaults.plan is None
    assert defaults.test is False
    assert defaults.preexisting is False

    full = parser.parse_args([
        "_orchestrator-task",
        ORCHESTRATOR_ID,
        "t1",
        "--resume-at",
        "review",
        "--plan",
        "plan.md",
        "--test",
        "--preexisting",
    ])
    assert full.resume_at == "review"
    assert full.plan == "plan.md"
    assert full.test is True
    assert full.preexisting is True


def test_worker_dispatches_orchestrator_task(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="orchestrator",
        request="do it",
        repo=tmp_path,
        worktree=tmp_path,
        branch="orchestrator/abc-t1",
    )
    store.update_background(
        record.id,
        launcher_pid=os.getpid(),
        nonce="n",
        argv=["_orchestrator-task", ORCHESTRATOR_ID, "t1", "--resume-at", "review"],
    )
    seen = {}

    async def fake_run_task_attempt(repo, run_id, args):  # ruff: ignore[unused-async]
        seen.update(repo=repo, run_id=run_id, resume_at=args.resume_at)
        store.transition(run_id, "complete")

    monkeypatch.setattr(
        "meow.execution.orchestration.task_worker.run_task_attempt",
        fake_run_task_attempt,
    )

    assert background.worker_main(tmp_path, record.id, "n") == 0

    assert seen == {"repo": tmp_path, "run_id": record.id, "resume_at": "review"}
    assert store.load(record.id).phase == "complete"


def test_worker_rejects_an_unknown_saved_invocation(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="x",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.update_background(
        record.id, launcher_pid=os.getpid(), nonce="n", argv=["status"]
    )

    with pytest.raises(background.BackgroundError, match="Saved worker invocation"):
        background.worker_main(tmp_path, record.id, "n")

    current = store.load(record.id)
    assert current.phase == "failed"
    assert current.results["failure"] == {"kind": "blocking", "cause": "invalid_input"}


def test_windows_detach_hides_the_console_without_detaching_it(tmp_path, monkeypatch):
    # DETACHED_PROCESS makes Windows ignore CREATE_NO_WINDOW, so a venv python.exe
    # launcher's child interpreter opens its own blank console window.
    seen = {}
    monkeypatch.setattr(background.sys, "platform", "win32")
    for name, value in (
        ("CREATE_NEW_PROCESS_GROUP", 0x200),
        ("CREATE_NO_WINDOW", 0x08000000),
        ("DETACHED_PROCESS", 0x8),
    ):
        monkeypatch.setattr(background.subprocess, name, value, raising=False)

    def popen(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(background.subprocess, "Popen", popen)

    background.detach_command(tmp_path, ["tool"])

    assert seen["creationflags"] & 0x08000000
    assert not seen["creationflags"] & 0x8


def test_worker_spawned_unbuffered(tmp_path, monkeypatch):
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append((argv, kwargs))
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(background.subprocess, "Popen", fake_popen)
    background.launch_background(
        tmp_path, ["run", "feature", "--name", "x", "--background"]
    )
    argv, kwargs = spawned[0]
    assert argv[:3] == [sys.executable, "-u", "-c"]
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
