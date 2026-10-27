"""Detached worker registration and safe liveness checks."""

import os
import sys
import time
from types import SimpleNamespace

import pytest

from meow.cli import cli as cli_core
from meow.cli.status_cli import render
from meow.execution.run_state import RunStore, reconcile_stale_runs
from meow.infrastructure import background, supervisor


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


def test_stale_pid_reconciles_as_parked(tmp_path, monkeypatch):
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
    parked = store.load(record.id)
    assert parked.phase == "parked"
    assert parked.park["kind"] == "crashed"
    assert parked.park["phase_before"] == "created"
    transition_count = len(store.load(record.id).transitions)
    background.reconcile_worker(store, record.id)
    assert len(store.load(record.id).transitions) == transition_count
    assert "Background worker: parked" in render(store.load(record.id))
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


def _drive_in_process(monkeypatch, repo, run_id):
    """Run the supervisor's `_drive` child in this process."""

    def child(_repo, command, env):
        assert command[3:6] == ["meow", "_drive", run_id]
        assert env[supervisor.SUPERVISOR_ENV] == "n"
        return background.drive_main(repo, run_id, "n")

    monkeypatch.setattr(supervisor, "_run_child", child)


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
    _drive_in_process(monkeypatch, tmp_path, record.id)
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

    _drive_in_process(monkeypatch, tmp_path, record.id)
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


def _driven_record(store, tmp_path, phase="generator_started"):
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(record.id, phase)
    store.set_driver(record.id)
    current = store.load(record.id)
    current.driver["start_identity"] = "a-process-that-exited"
    return store._write(current)


def test_dead_driver_parks_generator_started(tmp_path):
    store = RunStore(tmp_path)
    record = _driven_record(store, tmp_path)
    store.directory.joinpath(f"{record.id}.error.log").write_text(
        "".join(f"line {n}\n" for n in range(30)), encoding="utf-8"
    )

    assert reconcile_stale_runs(store) == [record.id]

    parked = store.load(record.id)
    assert parked.phase == "parked"
    assert parked.park["kind"] == "crashed"
    assert parked.park["cause"] == "driver_died"
    assert parked.park["phase_before"] == "generator_started"
    assert parked.last_failure.startswith("driver died during generator_started: ")
    assert "line 29" in parked.last_failure
    assert "line 10" in parked.last_failure
    assert "line 9\n" not in parked.last_failure
    assert parked.results["next_command"].startswith(f"meow resume {record.id}")
    assert reconcile_stale_runs(store) == []  # already settled


def test_live_driver_is_not_reconciled(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="f", repo=tmp_path, worktree=tmp_path, branch="d"
    )
    store.transition(record.id, "generator_started")
    store.set_driver(record.id)  # this test process: alive
    assert reconcile_stale_runs(store) == []
    assert store.load(record.id).phase == "generator_started"


def test_driverless_native_run_is_not_reconciled(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="native", request="f", repo=tmp_path, worktree=tmp_path, branch="d"
    )
    store.transition(record.id, "generator_started")
    assert reconcile_stale_runs(store) == []
    assert store.load(record.id).phase == "generator_started"


@pytest.mark.parametrize(
    "phase", ["complete", "failed", "cancelled", "interrupted_mutation"]
)
def test_settled_run_with_dead_driver_is_not_reconciled(tmp_path, phase):
    store = RunStore(tmp_path)
    record = _driven_record(store, tmp_path, phase)
    assert reconcile_stale_runs(store) == []
    assert store.load(record.id).phase == phase


def test_parked_run_with_dead_driver_keeps_its_park(tmp_path):
    store = RunStore(tmp_path)
    record = _driven_record(store, tmp_path)
    store.park(record.id, "rate limited", "transient", "rate_limit")
    before = store.load(record.id)
    assert reconcile_stale_runs(store) == []
    assert store.load(record.id).park == before.park


def test_killed_foreground_run_is_parked_on_next_status(tmp_path, capsys):
    store = RunStore(tmp_path)
    record = _driven_record(store, tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        cli_core.cli_main(["status", record.id, "--work-dir", str(tmp_path)])

    assert exit_info.value.code == 0
    assert store.load(record.id).phase == "parked"
    assert "Phase: parked" in capsys.readouterr().out


def _supervised_record(store, tmp_path):
    record = store.create(
        source="prompt", request="f", repo=tmp_path, worktree=tmp_path, branch="d"
    )
    store.update_background(
        record.id,
        nonce="n",
        pid=os.getpid(),
        start_identity=background._process_identity(os.getpid()),
        argv=["run", "f", "--name", "x", "--background"],
    )
    return record


def _crash(store, run_id):
    """What a hard-killed driver leaves behind: an active phase, no settle."""
    store.transition(run_id, "generator_started")
    return -9


def _fake_children(monkeypatch, store, run_id, outcomes):
    launches, sleeps = [], []

    def child(_repo, command, _env):
        launches.append(command[4])
        if command[4] == "resume":
            store.transition(run_id, "resuming")
        outcome = outcomes[min(len(launches), len(outcomes)) - 1]
        if outcome == "crash":
            return _crash(store, run_id)
        store.transition(run_id, outcome)
        return 0

    monkeypatch.setattr(supervisor, "_run_child", child)
    monkeypatch.setattr(supervisor, "_sleep", sleeps.append)
    return launches, sleeps


def test_supervisor_retries_parked_run(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = _supervised_record(store, tmp_path)
    launches, sleeps = _fake_children(
        monkeypatch, store, record.id, ["crash", "complete"]
    )

    assert supervisor.supervise(tmp_path, record.id, "n") == 0

    final = store.load(record.id)
    assert final.phase == "complete"
    assert final.park["attempts"] == 1
    assert final.park["kind"] == "crashed"
    assert launches == ["_drive", "resume"]
    assert sleeps == [60]


def test_supervisor_stops_after_auto_retries(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = _supervised_record(store, tmp_path)
    launches, sleeps = _fake_children(monkeypatch, store, record.id, ["crash"])

    assert supervisor.supervise(tmp_path, record.id, "n") == 1

    final = store.load(record.id)
    assert launches == ["_drive", "resume", "resume", "resume"]  # 3 relaunches
    assert sleeps == [60, 300, 900]
    assert final.phase == "failed"
    assert final.park["cause"] == "no_progress"
    assert final.park["repeated_cause"] == "driver_died"


def test_supervisor_honours_retry_after(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = _supervised_record(store, tmp_path)
    sleeps = []

    def child(_repo, command, _env):
        if command[4] == "_drive":
            store.transition(record.id, "generator_started")
            store.park(record.id, "429", "transient", "rate_limit", retry_after=1234)
        else:
            store.transition(record.id, "complete")
        return 0

    monkeypatch.setattr(supervisor, "_run_child", child)
    monkeypatch.setattr(supervisor, "_sleep", sleeps.append)
    assert supervisor.supervise(tmp_path, record.id, "n") == 0
    assert sleeps == [1234]


def test_supervisor_stops_when_resume_refuses_to_start(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = _supervised_record(store, tmp_path)
    launches = []

    def child(_repo, command, _env):
        launches.append(command[4])
        if command[4] == "_drive":
            _crash(store, record.id)
        return 1  # `meow resume` rejected the run without touching it

    monkeypatch.setattr(supervisor, "_run_child", child)
    monkeypatch.setattr(supervisor, "_sleep", lambda _seconds: None)
    assert supervisor.supervise(tmp_path, record.id, "n") == supervisor.EXIT_TEMPFAIL
    assert launches == ["_drive", "resume"]
    assert store.load(record.id).phase == "parked"


def test_supervisor_does_not_relaunch_a_run_cancelled_while_waiting(
    tmp_path, monkeypatch
):
    store = RunStore(tmp_path)
    record = _supervised_record(store, tmp_path)
    launches = []

    def child(_repo, command, _env):
        launches.append(command[4])
        return _crash(store, record.id)

    monkeypatch.setattr(supervisor, "_run_child", child)
    monkeypatch.setattr(
        supervisor, "_sleep", lambda _s: store.transition(record.id, "cancelled")
    )
    assert supervisor.supervise(tmp_path, record.id, "n") == 1
    assert launches == ["_drive"]


def test_supervisor_leaves_blocking_failure_failed(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = _supervised_record(store, tmp_path)
    _launches, sleeps = _fake_children(monkeypatch, store, record.id, ["failed"])
    assert supervisor.supervise(tmp_path, record.id, "n") == 1
    assert sleeps == []


def test_run_child_uses_its_own_process_group(tmp_path, monkeypatch):
    seen = {}

    def popen(command, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(wait=lambda timeout=None: 0)

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    assert supervisor._run_child(tmp_path, ["x"], {}) == 0
    assert seen["start_new_session"] is True


def test_windows_child_gets_a_new_process_group(tmp_path, monkeypatch):
    seen = {}
    new_group = 0x200
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(
        supervisor.subprocess, "CREATE_NEW_PROCESS_GROUP", new_group, raising=False
    )

    def popen(command, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(wait=lambda timeout=None: 0)

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    supervisor._run_child(tmp_path, ["x"], {})
    assert seen["creationflags"] == new_group
    assert seen["start_new_session"] is False


def test_foreground_run_is_supervised_under_claude_code(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    calls = []
    monkeypatch.setattr(
        cli_core, "run_supervised", lambda repo, argv: calls.append(argv) or 75
    )
    monkeypatch.setattr(
        cli_core, "_dispatch", lambda *_a, **_k: pytest.fail("ran unsupervised")
    )
    argv = ["run", "feature", "--name", "x", "-n", "--work-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as exit_info:
        cli_core.cli_main(argv)
    assert exit_info.value.code == supervisor.EXIT_TEMPFAIL
    assert calls == [argv]


def test_run_supervised_checkpoints_a_background_style_record(tmp_path, monkeypatch):
    seen = {}

    def fake_supervise(repo, run_id, nonce):
        record = RunStore(repo).load(run_id)
        seen.update(
            argv=record.background["argv"],
            nonce=record.background["nonce"] == nonce,
            pid=record.background["pid"],
        )
        return 0

    monkeypatch.setattr(supervisor, "supervise", fake_supervise)
    assert supervisor.run_supervised(tmp_path, ["run", "f", "--name", "x", "-n"]) == 0
    assert seen == {
        "argv": ["run", "f", "--name", "x", "-n", "--background"],
        "nonce": True,
        "pid": os.getpid(),
    }


def test_driver_without_start_identity_counts_as_alive(tmp_path):
    store = RunStore(tmp_path)
    record = _driven_record(store, tmp_path)
    current = store.load(record.id)
    current.driver = {"pid": 999_999_999, "start_identity": None}
    store._write(current)
    assert reconcile_stale_runs(store) == []
    assert store.load(record.id).phase == "generator_started"


class _SlowChild:
    """A child that finishes after two poll timeouts."""

    def __init__(self, interrupt=False):
        self.timeouts, self.interrupt, self.pid = [], interrupt, 4321

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if self.interrupt and len(self.timeouts) == 1:
            raise KeyboardInterrupt
        if timeout is not None and len(self.timeouts) < 3:  # ruff: ignore[magic-value-comparison]
            raise supervisor.subprocess.TimeoutExpired("x", timeout)
        return 0


def test_run_child_polls_so_windows_ctrl_c_is_seen(tmp_path, monkeypatch):
    child = _SlowChild()
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_a, **_k: child)
    assert supervisor._run_child(tmp_path, ["x"], {}) == 0
    assert child.timeouts == [0.5, 0.5, 0.5]


def test_run_child_forwards_ctrl_c(tmp_path, monkeypatch):
    child = _SlowChild(interrupt=True)
    interrupted = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_a, **_k: child)
    monkeypatch.setattr(supervisor, "_interrupt", interrupted.append)
    with pytest.raises(KeyboardInterrupt):
        supervisor._run_child(tmp_path, ["x"], {})
    assert interrupted == [child]


def test_windows_driver_turns_ctrl_break_into_keyboard_interrupt(monkeypatch):
    installed = {}
    monkeypatch.setattr(background.sys, "platform", "win32")
    monkeypatch.setattr(background.signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(
        background.signal,
        "signal",
        lambda sig, handler: installed.update({sig: handler}),
    )
    background._install_break_handler()
    with pytest.raises(KeyboardInterrupt):
        installed[21](21, None)


def test_jira_run_is_not_supervised_under_claude_code(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    dispatched = []
    monkeypatch.setattr(
        cli_core, "run_supervised", lambda *_a: pytest.fail("jira was supervised")
    )
    monkeypatch.setattr(
        cli_core, "_dispatch", lambda args, *_a, **_k: dispatched.append(args.jira)
    )
    cli_core.cli_main(["run", "--jira", "ABC-1", "--work-dir", str(tmp_path)])
    assert dispatched == ["ABC-1"]


def test_drive_failure_prints_one_line(tmp_path, monkeypatch, capsys):
    def fail(*_args):
        raise RuntimeError("worktree setup failed")

    monkeypatch.setattr(cli_core, "drive_main", fail)
    with pytest.raises(SystemExit) as exit_info:
        cli_core.cli_main([
            "_drive",
            "a" * 32,
            "--nonce",
            "n",
            "--work-dir",
            str(tmp_path),
        ])
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "worktree setup failed" in err
    assert "Traceback" not in err
