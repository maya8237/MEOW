"""The supervisor step: polling, merging, launching, and what it hands back.

Most tests script the collaborators (`children`, `integration`) so each row of
the state-transition table is checked in isolation; the last tests run the
real collaborators against a git repository."""

import asyncio
import os
from pathlib import Path

import pytest

from meow.common.process import process_identity
from meow.execution.orchestration import children as children_module
from meow.execution.orchestration import tick as tick_module
from meow.execution.orchestration.integration import MergeOutcome
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.orchestration.tick import (
    ConflictInfo,
    FailureInfo,
    block_task,
    record_conflict_resolved,
    tick,
)
from meow.execution.run_state import RunStore
from meow.infrastructure.background import inspect_worker
from meow.infrastructure.supervisor import SUPERVISOR_ENV
from tests.orchestration.fakes import (
    ScriptedLauncher,
    create_orchestrator,
    fail_child,
    finish_child,
    git,
    make_repository,
    orchestrator_config,
)

CONFIG = {
    "orchestrator": {
        "max_parallel": 3,
        "max_recovery_cycles": 3,
        "review_rounds": 3,
        "branch_prefix": "orchestrator/",
        "poll_seconds": 5,
        "open_pr": True,
    }
}


def make_task(task_id, *, deps=(), status="pending", cycles=0):
    runs = [] if status == "pending" else [f"run-{task_id}-1"]
    return OrchestratorTask(
        task_id,
        f"request {task_id}",
        depends_on=list(deps),
        status=status,
        cycles=cycles,
        run_ids=runs,
    )


def make_orchestrator(tmp_path, tasks, *, phase="running"):
    store = OrchestratorStore(tmp_path)
    record = store.create(
        objective="Build it",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )

    def mutate(item):
        item.phase = phase
        item.tasks = tasks
        item.integration["branch"] = f"orchestrator/{item.id}"
        item.integration["worktree"] = str(tmp_path / "integration")
        if phase == "blocked":
            item.blocker = {
                "task_id": tasks[-1].id,
                "cause": "needs-human",
                "evidence": [],
                "needed": "A decision",
            }

    store.update(record.id, mutate)
    return store, record.id


class World:
    """Scripted stand-ins for the collaborators `tick` calls."""

    def __init__(self, monkeypatch, store, orch_id, *, real_triage=False):
        self.store = store
        self.orch_id = orch_id
        self.polls = {}
        self.run_phases = {}
        self.merge_outcomes = {}
        self.merge_errors = {}
        self.finish_outcomes = {}
        self.launch_failures = set()
        self.launch_calls = []
        self.begin_calls = []
        self.finalized = []
        self.cancel_calls = []
        self.failure_calls = []
        patches = {
            "poll_task": self.poll_task,
            "finalize_passed": self.finalize_passed,
            "failure_evidence": self.failure_evidence,
            "cancel_children": self.cancel_children,
            "launch_attempt": self.launch_attempt,
            "begin_merge": self.begin_merge,
            "finish_merge": self.finish_merge,
            "run_phase": lambda repo, run_id: self.run_phases.get(run_id, "failed"),
        }
        if not real_triage:
            patches["record_failure"] = self.record_failure
        for name, replacement in patches.items():
            monkeypatch.setattr(tick_module, name, replacement)

    def poll_task(self, repo, task):
        return self.polls.get(task.id, "running")

    def finalize_passed(self, repo, task):
        self.finalized.append(task.id)
        return f"sha-{task.id}"

    @staticmethod
    def failure_evidence(repo, task, *, integration_log=None):
        found = [f"runs/{task.run_ids[-1]}.json"]
        return found + ([str(integration_log)] if integration_log else [])

    def cancel_children(self, repo, record):
        running = [t.id for t in record.tasks if t.status == "running"]
        self.cancel_calls.append(running)
        return running

    def launch_attempt(  # ruff: ignore[too-many-arguments] -- mirrors the real signature
        self, repo, store, orch_id, task_id, *, config, launcher
    ):
        self.launch_calls.append(task_id)
        failing = task_id in self.launch_failures

        def mutate(record):
            task = record.task(task_id)
            if failing:
                task.status = "failed"
                task.history.append({
                    "at": "t",
                    "action": "launch",
                    "cause": "launch-failed",
                    "rationale": "boom",
                    "evidence": [],
                })
                return
            task.run_ids.append(f"run-{task_id}-{len(task.run_ids) + 1}")
            task.status = "running"

        store.update(orch_id, mutate)
        return "" if failing else store.load(orch_id).task(task_id).run_ids[-1]

    def begin_merge(self, repo, store, orch_id, task_id):
        self.begin_calls.append(task_id)
        if task_id in self.merge_errors:
            raise self.merge_errors[task_id]
        store.update(
            orch_id, lambda record: record.integration.__setitem__("merging", task_id)
        )
        return self.merge_outcomes.get(task_id, MergeOutcome("merged"))

    async def finish_merge(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- mirrors the real signature
        self, repo, store, orch_id, task_id, config
    ):
        outcome = self.finish_outcomes.get(task_id, MergeOutcome("merged"))

        def mutate(record):
            record.integration["merging"] = None
            if outcome.status in {"merged", "noop"}:
                record.task(task_id).status = "merged"
                record.integration["merged"].append(task_id)

        store.update(orch_id, mutate)
        return outcome

    def record_failure(  # ruff: ignore[too-many-arguments] -- mirrors the real signature
        self, record, task_id, *, cause, evidence, max_cycles
    ):
        self.failure_calls.append((task_id, cause, tuple(evidence), max_cycles))
        record.task(task_id).history.append({
            "at": "t",
            "action": "failure",
            "cause": cause,
            "rationale": "",
            "evidence": list(evidence),
        })
        return "recorded" if record.phase == "blocked" else "triage"


def run_tick(tmp_path, store, orch_id, config=CONFIG):
    return asyncio.run(
        tick(tmp_path, store, orch_id, config, launcher=ScriptedLauncher())
    )


def statuses(store, orch_id):
    return {t.id: t.status for t in store.load(orch_id).tasks}


def test_first_tick_launches_ready_tasks_up_to_limit(tmp_path, monkeypatch):
    tasks = [make_task(name) for name in ("a", "b", "c", "d")]
    store, oid = make_orchestrator(tmp_path, tasks)
    world = World(monkeypatch, store, oid)

    result = run_tick(tmp_path, store, oid)

    assert result.state == "waiting"
    assert result.launched == ("a", "b", "c")
    assert result.running == ("a", "b", "c")
    assert world.launch_calls == ["a", "b", "c"]
    assert statuses(store, oid)["d"] == "pending"


def test_passed_child_is_merged_and_dependent_launched(tmp_path, monkeypatch):
    tasks = [make_task("a", status="running"), make_task("b", deps=["a"])]
    store, oid = make_orchestrator(tmp_path, tasks)
    world = World(monkeypatch, store, oid)
    world.polls["a"] = "passed"

    result = run_tick(tmp_path, store, oid)

    assert world.finalized == ["a"]
    assert result.merged == ("a",)
    assert result.launched == ("b",)
    assert result.state == "waiting"
    assert statuses(store, oid) == {"a": "merged", "b": "running"}
    assert store.load(oid).integration["merged"] == ["a"]


def test_conflict_stops_merging_and_reports_paths(tmp_path, monkeypatch):
    tasks = [make_task("a", status="passed"), make_task("b", status="passed")]
    store, oid = make_orchestrator(tmp_path, tasks)
    world = World(monkeypatch, store, oid)
    world.merge_outcomes["a"] = MergeOutcome("conflict", conflicted_paths=("s.py",))

    result = run_tick(tmp_path, store, oid)

    assert result.state == "conflicts"
    assert result.conflicts == (ConflictInfo("a", ("s.py",), "request a", ()),)
    assert world.begin_calls == ["a"]
    assert store.load(oid).integration["merging"] == "a"
    assert statuses(store, oid) == {"a": "passed", "b": "passed"}


def test_open_merge_is_reported_again_without_a_new_merge(tmp_path, monkeypatch):
    tasks = [make_task("a", status="passed"), make_task("b", status="merged")]
    store, oid = make_orchestrator(tmp_path, tasks)
    store.update(
        oid,
        lambda record: record.integration.update(merging="a", merged=["b"]),
    )
    world = World(monkeypatch, store, oid)
    monkeypatch.setattr(tick_module, "conflicted_paths", lambda path: ("x.py",))

    result = run_tick(tmp_path, store, oid)

    assert result.state == "conflicts"
    assert result.conflicts == (
        ConflictInfo("a", ("x.py",), "request a", ("request b",)),
    )
    assert world.begin_calls == []


def test_failed_child_reported_for_triage_with_evidence(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="running")])
    world = World(monkeypatch, store, oid)
    world.polls["a"] = "failed"
    world.run_phases["run-a-1"] = "failed"

    result = run_tick(tmp_path, store, oid)

    assert result.state == "failures"
    assert result.failures == (
        FailureInfo("a", "child-failed", ("runs/run-a-1.json",)),
    )
    assert world.failure_calls == [("a", "child-failed", ("runs/run-a-1.json",), 3)]
    assert statuses(store, oid) == {"a": "failed"}


def test_dead_worker_is_reported_as_worker_died(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="running")])
    world = World(monkeypatch, store, oid)
    world.polls["a"] = "failed"
    world.run_phases["run-a-1"] = "interrupted_mutation"

    result = run_tick(tmp_path, store, oid)

    assert result.failures[0].cause == "worker-died"


def test_failing_post_merge_checks_report_integration_failure(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="passed")])
    world = World(monkeypatch, store, oid)
    log = Path("integration-a-1.log")
    world.finish_outcomes["a"] = MergeOutcome("checks_failed", log=log)

    result = run_tick(tmp_path, store, oid)

    assert result.state == "failures"
    assert result.failures[0].cause == "integration"
    assert "integration-a-1.log" in result.failures[0].evidence
    assert result.merged == ()
    assert statuses(store, oid) == {"a": "failed"}


def test_launch_failure_is_recorded_and_reported_for_triage(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a")])
    world = World(monkeypatch, store, oid)
    world.launch_failures.add("a")

    result = run_tick(tmp_path, store, oid)

    assert result.state == "failures"
    assert result.launched == ()
    assert result.failures == (FailureInfo("a", "launch-failed", ()),)
    assert world.failure_calls == [("a", "launch-failed", (), 3)]


def test_git_merge_failure_is_an_integration_failure(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="passed")])
    world = World(monkeypatch, store, oid)
    world.merge_errors["a"] = RuntimeError("git merge failed: bad object")

    result = run_tick(tmp_path, store, oid)

    log = store.dir_for(oid) / "integration-a-error.log"
    assert result.state == "failures"
    assert result.failures[0].cause == "integration"
    assert str(log) in result.failures[0].evidence
    assert log.read_text(encoding="utf-8") == "git merge failed: bad object\n"


def test_other_merge_errors_propagate(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="passed")])
    world = World(monkeypatch, store, oid)
    world.merge_errors["a"] = RuntimeError("Integration worktree is not set up")

    with pytest.raises(RuntimeError, match="not set up"):
        run_tick(tmp_path, store, oid)


def test_all_merged(tmp_path, monkeypatch):
    tasks = [make_task("a", status="merged"), make_task("b", status="merged")]
    store, oid = make_orchestrator(tmp_path, tasks)
    world = World(monkeypatch, store, oid)

    result = run_tick(tmp_path, store, oid)

    assert result.state == "all_merged"
    assert world.launch_calls == []


def test_blocked_waits_for_running_children_then_reports_blocked(tmp_path, monkeypatch):
    tasks = [make_task("a", status="running"), make_task("b", status="blocked")]
    store, oid = make_orchestrator(tmp_path, tasks, phase="blocked")
    world = World(monkeypatch, store, oid)

    first = run_tick(tmp_path, store, oid)
    world.polls["a"] = "passed"
    second = run_tick(tmp_path, store, oid)

    assert first.state == "waiting"
    assert first.running == ("a",)
    assert second.state == "blocked"
    assert second.merged == ("a",)
    assert world.launch_calls == []


def test_blocked_child_failure_is_recorded_not_triaged(tmp_path, monkeypatch):
    tasks = [make_task("a", status="running"), make_task("b", status="blocked")]
    store, oid = make_orchestrator(tmp_path, tasks, phase="blocked")
    world = World(monkeypatch, store, oid)
    world.polls["a"] = "failed"

    result = run_tick(tmp_path, store, oid)

    assert result.state == "blocked"
    assert result.failures == ()
    assert [call[:2] for call in world.failure_calls] == [("a", "child-failed")]
    assert statuses(store, oid) == {"a": "failed", "b": "blocked"}


def test_cancel_marker_cancels_children(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="running")])
    world = World(monkeypatch, store, oid)
    marker = store.cancel_marker(oid)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("cancel\n", encoding="utf-8")

    result = run_tick(tmp_path, store, oid)

    assert result.state == "cancelled"
    assert store.load(oid).phase == "cancelled"
    assert world.cancel_calls == [["a"]]
    assert world.launch_calls == []


def test_tick_refuses_phases_it_does_not_supervise(tmp_path, monkeypatch):
    store, oid = make_orchestrator(
        tmp_path, [make_task("a", status="merged")], phase="reviewing"
    )
    World(monkeypatch, store, oid)

    with pytest.raises(ValueError, match="tick runs only"):
        run_tick(tmp_path, store, oid)


def test_stalled_orchestrator_raises(tmp_path, monkeypatch):
    tasks = [make_task("a", deps=["b"]), make_task("b", deps=["a"])]
    store, oid = make_orchestrator(tmp_path, tasks)
    World(monkeypatch, store, oid)

    with pytest.raises(RuntimeError, match="stalled"):
        run_tick(tmp_path, store, oid)


def test_block_task_sets_phase_blocker_and_history(tmp_path):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="failed")])
    blocker = {
        "task_id": "a",
        "cause": "invalid-decision",
        "evidence": ["x.json"],
        "needed": "Fix the decision",
    }

    store.update(oid, lambda record: block_task(record, blocker))

    record = store.load(oid)
    assert (record.phase, record.blocker) == ("blocked", blocker)
    assert record.task("a").status == "blocked"
    assert record.task("a").history[-1]["cause"] == "invalid-decision"


def test_record_conflict_resolved_is_journaled_on_the_task(tmp_path):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="passed")])

    record_conflict_resolved(store, oid, "a", ("s.py", "t.py"))

    entry = store.load(oid).task("a").history[-1]
    assert entry["action"] == "conflict-resolved"
    assert entry["rationale"] == "Resolved conflicts in s.py, t.py"
    assert entry["cause"] == ""


def test_real_record_failure_round_trips_into_failure_info(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", status="running")])
    world = World(monkeypatch, store, oid, real_triage=True)
    world.polls["a"] = "failed"
    world.run_phases["run-a-1"] = "failed"

    result = run_tick(tmp_path, store, oid)

    assert result.state == "failures"
    assert result.failures == (
        FailureInfo("a", "child-failed", ("runs/run-a-1.json",)),
    )


def test_real_record_failure_at_the_cycle_limit_blocks(tmp_path, monkeypatch):
    store, oid = make_orchestrator(
        tmp_path, [make_task("a", status="running", cycles=3)]
    )
    world = World(monkeypatch, store, oid, real_triage=True)
    world.polls["a"] = "failed"
    world.run_phases["run-a-1"] = "failed"

    result = run_tick(tmp_path, store, oid)

    record = store.load(oid)
    assert result.state == "blocked"
    assert record.blocker["cause"] == "recovery-exhausted"
    assert record.task("a").status == "blocked"


def test_real_launch_failure_at_the_cycle_limit_blocks(tmp_path, monkeypatch):
    store, oid = make_orchestrator(tmp_path, [make_task("a", cycles=3)])
    world = World(monkeypatch, store, oid, real_triage=True)
    world.launch_failures.add("a")

    result = run_tick(tmp_path, store, oid)

    record = store.load(oid)
    assert result.state == "blocked"
    assert record.blocker["cause"] == "recovery-exhausted"
    assert record.task("a").status == "blocked"


def real_orchestrator(tmp_path, tasks):
    repo, _remote = make_repository(tmp_path)
    store, oid = create_orchestrator(repo, tasks)
    return repo, store, oid, orchestrator_config(repo)


def test_real_children_merge_a_passed_task_and_launch_its_dependent(tmp_path):
    tasks = [
        OrchestratorTask("api", "Build the api"),
        OrchestratorTask("ui", "Build the ui", depends_on=["api"]),
    ]
    repo, store, oid, config = real_orchestrator(tmp_path, tasks)
    launcher = ScriptedLauncher()

    first = asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    finish_child(repo, launcher.run_ids("api")[0], {"api.py": "x = 1\n"})
    second = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    record = store.load(oid)
    integration = Path(record.integration["worktree"])
    assert (first.state, first.launched) == ("waiting", ("api",))
    assert (second.merged, second.launched) == (("api",), ("ui",))
    assert (integration / "api.py").read_text(encoding="utf-8") == "x = 1\n"
    assert git(integration, "log", "-1", "--format=%s").startswith(
        "Merge orchestrator task api"
    )
    ui_worktree = launcher.launches[1]["worktree"]
    assert (ui_worktree / "api.py").exists()


def test_real_children_report_failed_child_with_run_journal_evidence(tmp_path):
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )
    launcher = ScriptedLauncher()
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    run_id = launcher.run_ids("api")[0]
    fail_child(repo, run_id, "tests failed")

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    assert result.state == "failures"
    assert result.failures[0].cause == "child-failed"
    assert any(run_id in path for path in result.failures[0].evidence)


def test_real_launch_error_is_reported_as_launch_failed(tmp_path):
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )
    launcher = ScriptedLauncher()
    launcher.fail_for.add("api")

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    assert result.state == "failures"
    assert result.failures[0].task_id == "api"
    assert result.failures[0].cause == "launch-failed"
    assert launcher.launches == []


class FakeResumeSpawner:
    """Stands in for the detached `meow resume RUN --continue` spawn."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, repo, store, run_id):
        self.calls.append((repo, run_id))
        # A process that is already gone, so the next poll sees it settled.
        store.update_background(run_id, pid=1, start_identity="gone", state="running")


def park_child(repo, run_id):
    RunStore(repo).park(run_id, "WinError 1450", "resource", "resource")


def test_tick_resumes_parked_child_before_triage(tmp_path, monkeypatch):
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )
    spawner = FakeResumeSpawner()
    monkeypatch.setattr(children_module, "_spawn_resume", spawner)
    launcher = ScriptedLauncher()
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    run_id = launcher.run_ids("api")[0]
    before = store.load(oid).task("api")
    park_child(repo, run_id)

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    task = store.load(oid).task("api")
    assert result.state == "waiting"
    assert result.failures == ()
    assert spawner.calls == [(repo, run_id)]
    assert task.status == "running"
    assert (task.run_ids, task.branch, task.worktree) == (
        [run_id],
        before.branch,
        before.worktree,
    )
    resumed = [e for e in task.history if e["action"] == "child_resumed"]
    assert len(resumed) == 1
    assert resumed[0]["run_id"] == run_id
    assert not any(e.get("cause") == "worker-died" for e in task.history)


def test_tick_triages_after_child_budget(tmp_path, monkeypatch):
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )
    config = {**config, "run": {**config["run"], "managed_resumes": 1}}
    spawner = FakeResumeSpawner()
    monkeypatch.setattr(children_module, "_spawn_resume", spawner)
    launcher = ScriptedLauncher()
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    run_id = launcher.run_ids("api")[0]
    park_child(repo, run_id)
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    park_child(repo, run_id)  # the resume parked again

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    task = store.load(oid).task("api")
    assert len(spawner.calls) == 1
    assert result.state == "failures"
    assert result.failures[0].cause == "worker-died"
    assert task.status == "failed"
    skipped = [e for e in task.history if e["action"] == "child_resume_skipped"]
    assert skipped[-1]["rationale"] == "managed_resumes reached"


def test_tick_triages_blocking_child_failure_without_resume(tmp_path, monkeypatch):
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )
    spawner = FakeResumeSpawner()
    monkeypatch.setattr(children_module, "_spawn_resume", spawner)
    launcher = ScriptedLauncher()
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    fail_child(repo, launcher.run_ids("api")[0], "invalid config")

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    assert spawner.calls == []
    assert result.failures[0].cause == "child-failed"


def test_tick_triages_when_resume_cannot_start(tmp_path, monkeypatch):
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )

    def broken(repo, store, run_id):
        raise OSError("no handles left")

    monkeypatch.setattr(children_module, "_spawn_resume", broken)
    launcher = ScriptedLauncher()
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    park_child(repo, launcher.run_ids("api")[0])

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    task = store.load(oid).task("api")
    assert result.failures[0].cause == "worker-died"
    failed = [e for e in task.history if e["action"] == "child_resume_failed"]
    assert "no handles left" in failed[0]["rationale"]


def launched_and_parked(tmp_path, monkeypatch, *, run=None):
    """A real orchestrator whose one child run has parked; a fake spawner."""
    repo, store, oid, config = real_orchestrator(
        tmp_path, [OrchestratorTask("api", "Build the api")]
    )
    if run:
        config = {**config, "run": {**config["run"], **run}}
    spawner = FakeResumeSpawner()
    monkeypatch.setattr(children_module, "_spawn_resume", spawner)
    launcher = ScriptedLauncher()
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    park_child(repo, launcher.run_ids("api")[0])
    return repo, store, oid, config, spawner, launcher


def actions(store, oid, action):
    return [e for e in store.load(oid).task("api").history if e["action"] == action]


def test_retry_gets_fresh_managed_budget_but_task_total_caps_it(tmp_path, monkeypatch):
    repo, store, oid, config, spawner, launcher = launched_and_parked(
        tmp_path, monkeypatch, run={"managed_resumes": 1, "max_total_resumes": 2}
    )

    def run_tick_once():
        return asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    def retry_and_park():
        # What triage's `retry` does: a fresh attempt, i.e. a new child run.
        store.update(oid, lambda r: setattr(r.task("api"), "status", "pending"))
        run_tick_once()
        park_child(repo, launcher.run_ids("api")[-1])

    run_tick_once()  # run 1: resumed
    park_child(repo, launcher.run_ids("api")[0])
    assert run_tick_once().failures[0].cause == "worker-died"
    retry_and_park()
    run_tick_once()  # run 2: a fresh managed budget, resumed again
    assert [call[1] for call in spawner.calls] == launcher.run_ids("api")
    retry_and_park()

    result = run_tick_once()  # run 3: the task-wide cap stops it

    assert [call[1] for call in spawner.calls] == launcher.run_ids("api")[:2]
    assert result.failures[0].cause == "worker-died"
    skipped = actions(store, oid, "child_resume_skipped")
    assert skipped[0]["rationale"] == "managed_resumes reached"
    assert skipped[-1]["rationale"] == "max_total_resumes reached"
    assert skipped[-1]["run_id"] == launcher.run_ids("api")[2]


def test_blocking_park_is_triaged_without_resume(tmp_path, monkeypatch):
    repo, store, oid, config, spawner, launcher = launched_and_parked(
        tmp_path, monkeypatch
    )
    RunStore(repo).park(launcher.run_ids("api")[0], "bad key", "blocking", "auth")

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    assert spawner.calls == []
    assert result.failures[0].cause == "worker-died"
    skipped = actions(store, oid, "child_resume_skipped")
    assert skipped[0]["rationale"] == "blocking failure: auth"


def test_live_resumed_worker_keeps_polling_running(tmp_path, monkeypatch):
    repo, store, oid, config, spawner, launcher = launched_and_parked(
        tmp_path, monkeypatch
    )
    run_id = launcher.run_ids("api")[0]

    def live(repo, runs, run_id):
        spawner.calls.append((repo, run_id))
        pid = os.getpid()
        runs.update_background(
            run_id, pid=pid, start_identity=process_identity(pid), state="running"
        )

    monkeypatch.setattr(children_module, "_spawn_resume", live)
    asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    result = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    assert spawner.calls == [(repo, run_id)]
    assert result.state == "waiting"
    assert result.running == ("api",)
    assert len(actions(store, oid, "child_resumed")) == 1


def test_live_driver_skips_without_spending_budget(tmp_path, monkeypatch):
    repo, store, oid, config, spawner, launcher = launched_and_parked(
        tmp_path, monkeypatch
    )
    run_id = launcher.run_ids("api")[0]
    RunStore(repo).set_driver(run_id)  # this live process drives it

    first = asyncio.run(tick(repo, store, oid, config, launcher=launcher))
    second = asyncio.run(tick(repo, store, oid, config, launcher=launcher))

    assert spawner.calls == []
    assert (first.state, second.state) == ("waiting", "waiting")
    assert store.load(oid).task("api").status == "running"
    skipped = actions(store, oid, "child_resume_skipped")
    assert [e["rationale"] for e in skipped] == [children_module.LIVE_DRIVER]


def test_spawn_resume_registers_the_detached_worker(tmp_path, monkeypatch):
    repo, store, oid, _config, _spawner, launcher = launched_and_parked(
        tmp_path, monkeypatch
    )
    monkeypatch.undo()
    run_id = launcher.run_ids("api")[0]
    runs = RunStore(repo)
    runs.update_background(run_id, launch_started_at=0.0)  # long ago
    seen = {}

    def detach(working_dir, argv, log=None, env=None):
        seen.update(argv=argv, log=log, env=env, cwd=working_dir)
        seen["nonce"] = runs.load(run_id).background["nonce"]
        return {"pid": 4242, "log": log, "argv": argv}

    monkeypatch.setattr(children_module, "detach_command", detach)
    monkeypatch.setattr(children_module, "process_identity", lambda pid: None)

    assert children_module.resume_attempt(repo, store, oid, "api") == run_id

    background = runs.load(run_id).background
    assert seen["env"][SUPERVISOR_ENV] == seen["nonce"] == background["nonce"]
    assert seen["argv"][-5:] == [
        "resume",
        run_id,
        "--continue",
        "--work-dir",
        str(repo),
    ]
    assert seen["cwd"] == repo
    assert (background["pid"], background["start_identity"]) == (4242, None)
    assert background["launch_started_at"] > 0  # refreshed: within startup grace
    record = runs.load(run_id)
    assert inspect_worker(record, include_terminal=True).state == "starting"


def test_spawn_resume_kills_worker_it_cannot_register(tmp_path, monkeypatch):
    repo, store, oid, _config, _spawner, _launcher = launched_and_parked(
        tmp_path, monkeypatch
    )
    monkeypatch.undo()
    killed = []
    monkeypatch.setattr(
        children_module,
        "detach_command",
        lambda working_dir, argv, log=None, env=None: {"pid": 4243},
    )
    monkeypatch.setattr(children_module, "_terminate", killed.append)
    original = RunStore.update_background

    def update(self, rid, **patch):
        if "pid" in patch:
            raise OSError("access denied")
        return original(self, rid, **patch)

    monkeypatch.setattr(RunStore, "update_background", update)

    assert children_module.resume_attempt(repo, store, oid, "api") == ""

    assert killed == [4243]
    failed = actions(store, oid, "child_resume_failed")
    assert "access denied" in failed[0]["rationale"]
