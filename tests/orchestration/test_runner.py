"""The CLI driver's phase machine, with scripted agents and simulated children."""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from meow.execution.orchestration import runner as runner_module
from meow.execution.orchestration.intake import IntakeRequest, jira_task
from meow.execution.orchestration.report import (
    EXIT_BLOCKED,
    EXIT_DELIVERED_WITHOUT_PR,
    EXIT_FAILED,
)
from meow.execution.orchestration.runner import cancel_orchestrator
from meow.execution.orchestration.store import DriverLock
from meow.execution.run_state import RunStore
from tests.orchestration.driver_fakes import (
    AgentScript,
    DriverKilled,
    Simulation,
    World,
    task_dict,
)
from tests.orchestration.fakes import git

PASS_API = ("pass", {"api.py": "x = 1\n"})
BLOCKED = {"action": "blocked", "rationale": "needs a human", "needed": "credentials"}
RETRY = {"action": "retry", "rationale": "flaky"}
ATTEMPTS = 2
THREE = 3


def one_task_world(tmp_path, monkeypatch, outcomes, decisions=()):
    script = AgentScript([task_dict("api")], decisions=decisions)
    return World(tmp_path, monkeypatch, script, {"api": outcomes})


def test_dry_run_stops_after_decomposition(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])

    oid, code = world.start(dry_run=True)

    record = world.record(oid)
    assert code == 0
    assert record.phase == "decomposing"
    assert record.intake["dry_run"] is True
    assert [t.id for t in record.tasks] == ["api"]
    assert world.sim.launcher.launches == []
    assert (world.store.dir_for(oid) / "report.md").is_file()


def test_jira_fetch_failure_fails_before_worktree(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])

    async def no_issues(repo, config, request, agents):  # ruff: ignore[unused-async]
        raise RuntimeError("No Jira issues matched")

    monkeypatch.setattr(runner_module, "fetch_jira_tasks", no_issues)

    oid, code = world.start(intake=IntakeRequest("jira", jira_keys=("PROJ-1",)))

    record = world.record(oid)
    assert code == EXIT_FAILED
    assert record.phase == "failed"
    assert record.error == {
        "phase": "decomposing",
        "message": "No Jira issues matched",
        "kind": "transient",
        "cause": "error",
    }
    assert not (world.repo / ".worktrees").exists()
    assert world.sim.launcher.launches == []


def test_detached_head_requires_from(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    git(world.repo, "checkout", "-q", "--detach")

    with pytest.raises(ValueError, match="detached"):
        world.start()
    assert world.store.records() == []

    _oid, code = world.start(base_branch="main", dry_run=True)
    assert code == 0


def test_exactly_one_input_source_is_required(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])

    with pytest.raises(ValueError, match="exactly one"):
        world.start(intake=IntakeRequest("objective"))
    assert world.store.records() == []


def test_triage_retry_then_success_delivers(tmp_path, monkeypatch):
    world = one_task_world(
        tmp_path, monkeypatch, [("fail", "boom"), PASS_API], decisions=[RETRY]
    )

    oid, code = world.start()

    record = world.record(oid)
    assert code == 0
    assert record.phase == "delivered"
    assert record.task("api").cycles == 1
    assert len(record.task("api").run_ids) == ATTEMPTS
    assert world.script.triage_calls[0]["cause"] == "child-failed"
    assert any(
        record.task("api").run_ids[0] in path
        for path in world.script.triage_calls[0]["evidence"]
    )
    assert git(world.remote, "show", f"orchestrator/{oid}:api.py") == "x = 1"
    assert record.delivery["pr"] == {
        "url": "https://example.test/pr/1",
        "provider": "github",
    }


def test_triage_agent_failure_reports_original_cause(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [("fail", "worker failed")])
    reason = "Session limit reached; resets at 11:30am"
    agent = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError(reason)))
    world.script.agents.triage = lambda context: agent

    oid, code = world.start()

    record = world.record(oid)
    assert code == EXIT_BLOCKED
    assert record.blocker["cause"] == "triage-agent-failed"
    assert reason in record.blocker["needed"]
    assert reason in (world.store.dir_for(oid) / "report.md").read_text(
        encoding="utf-8"
    )
    assert record.delivery.get("pushed_commit") is None


def test_blocked_pushes_nothing_and_exits_2(tmp_path, monkeypatch):
    world = one_task_world(
        tmp_path, monkeypatch, [("fail", "boom")], decisions=[BLOCKED]
    )

    oid, code = world.start()

    record = world.record(oid)
    assert code == EXIT_BLOCKED
    assert record.phase == "blocked"
    assert record.blocker["cause"] == "needs-human"
    assert record.blocker["needed"] == "credentials"
    assert world.pushed() == ""
    report = (world.store.dir_for(oid) / "report.md").read_text(encoding="utf-8")
    assert report.startswith(f"# Orchestrator {oid}: blocked")


def test_recovery_exhausted_blocks_without_another_triage(tmp_path, monkeypatch):
    failures = [("fail", f"boom {n}") for n in range(THREE + 1)]
    # Identical retries would trip `repeated-decision` first; vary the notes.
    distinct = [{**RETRY, "notes": f"try {n}"} for n in range(THREE)]
    world = one_task_world(tmp_path, monkeypatch, failures, decisions=distinct)

    oid, code = world.start()

    record = world.record(oid)
    assert code == EXIT_BLOCKED
    assert record.blocker["cause"] == "recovery-exhausted"
    assert len(world.script.triage_calls) == THREE
    assert world.pushed() == ""


def test_invalid_triage_decision_blocks_the_task(tmp_path, monkeypatch):
    world = one_task_world(
        tmp_path, monkeypatch, [("fail", "boom")], decisions=["not json"]
    )

    oid, code = world.start()

    record = world.record(oid)
    assert code == EXIT_BLOCKED
    assert record.blocker["cause"] == "invalid-decision"
    assert record.blocker["task_id"] == "api"
    assert record.task("api").status == "blocked"


def test_unresolved_conflict_rolls_back_and_goes_to_triage(tmp_path, monkeypatch):
    tasks = [task_dict("a"), task_dict("b")]
    script = AgentScript(
        tasks,
        decisions=[BLOCKED],
        resolutions={
            "shared.txt": "<<<<<<< ours\nleft\n=======\nright\n>>>>>>> theirs\n"
        },
    )
    outcomes = {
        "a": [("pass", {"shared.txt": "a\n"})],
        "b": [("pass", {"shared.txt": "b\n"})],
    }
    world = World(tmp_path, monkeypatch, script, outcomes)

    oid, code = world.start()

    record = world.record(oid)
    integration = record.integration["worktree"]
    merge_head = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        cwd=integration,
        capture_output=True,
        check=False,
    )
    assert code == EXIT_BLOCKED
    assert script.triage_calls[0]["cause"] == "integration"
    assert merge_head.returncode != 0
    assert git(integration, "status", "--porcelain") == ""
    assert record.integration["merged"] == ["a"]
    assert world.pushed() == ""


def test_push_failure_marks_failed_and_resume_retries_delivery(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    git(world.repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    oid, code = world.start()

    failed = world.record(oid)
    assert code == EXIT_FAILED
    assert failed.phase == "failed"
    assert failed.error["phase"] == "delivering"
    git(world.repo, "remote", "set-url", "origin", str(world.remote))

    assert world.resume(oid) == 0

    record = world.record(oid)
    assert record.phase == "delivered"
    assert record.error is None
    assert git(world.remote, "show", f"orchestrator/{oid}:api.py") == "x = 1"


def test_resumed_delivery_reviews_code_changed_after_push_failure(
    tmp_path, monkeypatch
):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    git(world.repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    oid, code = world.start()
    assert code == EXIT_FAILED
    worktree = Path(world.record(oid).integration["worktree"])
    (worktree / "api.py").write_text("x = 2\n")
    git(worktree, "add", "api.py")
    git(worktree, "commit", "-m", "late edit")
    git(world.repo, "remote", "set-url", "origin", str(world.remote))
    assert world.resume(oid) == 0
    assert world.record(oid).review["round"] == ATTEMPTS
    assert git(world.remote, "show", f"orchestrator/{oid}:api.py") == "x = 2"


def test_resume_reattaches_a_live_child(tmp_path, monkeypatch):
    script = AgentScript([task_dict("a"), task_dict("b")])
    outcomes = {"a": [PASS_API], "b": [("hang",)]}
    world = World(tmp_path, monkeypatch, script, outcomes, kill_after=3)
    with pytest.raises(DriverKilled):
        world.start()
    oid = world.store.latest().id
    assert world.record(oid).task("b").status == "running"
    resumed = Simulation(
        world.repo,
        {"a": [PASS_API], "b": [("pass", {"b.py": "y = 2\n"})]},
        launcher=world.sim.launcher,
    )
    world.sim = resumed

    assert world.resume(oid) == 0

    record = world.record(oid)
    assert len(record.task("b").run_ids) == 1
    assert len(world.sim.launcher.run_ids("b")) == 1
    assert record.phase == "delivered"


def test_resume_dead_child_goes_to_triage(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [("hang",)], decisions=[RETRY])
    world.sim.kill_after = 2
    with pytest.raises(DriverKilled):
        world.start()
    oid = world.store.latest().id
    run_id = world.record(oid).task("api").run_ids[0]
    RunStore(world.repo).transition(run_id, "interrupted_mutation")
    world.sim = Simulation(
        world.repo, {"api": [("hang",), PASS_API]}, launcher=world.sim.launcher
    )

    assert world.resume(oid) == 0

    assert world.script.triage_calls[0]["cause"] == "worker-died"
    assert len(world.record(oid).task("api").run_ids) == ATTEMPTS


def test_resume_blocked_resets_cycles_and_replaces_request(tmp_path, monkeypatch):
    world = one_task_world(
        tmp_path, monkeypatch, [("fail", "boom"), PASS_API], decisions=[BLOCKED]
    )
    oid, code = world.start()
    assert code == EXIT_BLOCKED

    assert world.resume(oid, task_id="api", request="Build it differently") == 0

    record = world.record(oid)
    task = record.task("api")
    assert record.phase == "delivered"
    assert record.blocker is None
    assert task.request == "Build it differently"
    assert task.cycles == 0
    assert [e["action"] for e in task.history if e["action"] == "resume"] == ["resume"]
    assert len(task.run_ids) == ATTEMPTS


def test_resume_mid_merge_resets_first(tmp_path, monkeypatch):
    tasks = [task_dict("a"), task_dict("b")]
    script = AgentScript(tasks, resolutions={"shared.txt": "a+b\n"})
    outcomes = {
        "a": [("pass", {"shared.txt": "a\n"})],
        "b": [("pass", {"shared.txt": "b\n"})],
    }
    world = World(tmp_path, monkeypatch, script, outcomes)
    script.integrator_error = DriverKilled()
    with pytest.raises(DriverKilled):
        world.start()
    oid = world.store.latest().id
    integration = world.record(oid).integration["worktree"]
    assert world.record(oid).integration["merging"] == "b"
    assert (
        subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            cwd=integration,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    script.integrator_error = None

    assert world.resume(oid) == 0

    record = world.record(oid)
    assert record.integration["merging"] is None
    assert record.integration["merged"] == ["a", "b"]
    assert git(world.remote, "show", f"orchestrator/{oid}:shared.txt") == "a+b"
    assert len(script.integrator_calls) == ATTEMPTS


def test_repeated_delivery_is_idempotent(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    oid, code = world.start()
    assert code == 0
    tip = git(world.remote, "rev-parse", f"orchestrator/{oid}")

    assert world.resume(oid) == 0
    assert world.resume(oid) == 0

    assert len(world.script.pull_request.calls) == 1
    assert git(world.remote, "rev-parse", f"orchestrator/{oid}") == tip


def test_resume_of_a_delivered_orchestrator_retries_only_a_failed_pr(
    tmp_path, monkeypatch
):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    world.script.pull_request.error = "rate limited"
    oid, code = world.start()
    assert code == EXIT_DELIVERED_WITHOUT_PR
    assert world.record(oid).delivery["pr_error"] == "rate limited"
    world.script.pull_request.error = None

    assert world.resume(oid) == 0

    record = world.record(oid)
    assert record.delivery["pr"]["url"] == "https://example.test/pr/1"
    assert record.delivery["pr_error"] is None
    assert len(world.script.pull_request.calls) == ATTEMPTS


def test_no_pr_option_exits_0_without_pr(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])

    oid, code = world.start(open_pr=False)

    record = world.record(oid)
    assert code == 0
    assert record.phase == "delivered"
    assert record.delivery["pr"] is None
    assert world.script.pull_request.calls == []
    assert git(world.remote, "show", f"orchestrator/{oid}:api.py") == "x = 1"


def test_blocking_driver_exception_is_recorded_as_blocking(tmp_path, monkeypatch):
    from meow.execution.run_failures import BlockingRunError

    world = one_task_world(tmp_path, monkeypatch, [("hang",)])

    async def explode(seconds):  # ruff: ignore[unused-async]
        raise BlockingRunError("no auth", cause="auth")

    world.sim.sleep = explode
    with pytest.raises(BlockingRunError):
        world.start()

    error = world.store.latest().error
    assert (error["kind"], error["cause"]) == ("blocking", "auth")


def test_resume_of_a_failed_orchestrator_spends_the_retry_budget(tmp_path, monkeypatch):
    from meow.execution.orchestration.runner import prepare_resume

    world = one_task_world(tmp_path, monkeypatch, [("hang",)])
    record = world.store.create(
        objective="x",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=1,
        options={"test": False, "pr": True},
    )
    record.phase = "failed"
    record.error = {"phase": "running", "message": "m", "kind": "transient"}
    prepare_resume(record, None, None)
    assert (record.phase, record.error) == ("running", None)
    assert record.options["failed_resumes"] == 1


def test_driver_exception_marks_failed_and_reraises(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [("hang",)])

    async def explode(seconds):  # ruff: ignore[unused-async]
        raise RuntimeError("boom")

    world.sim.sleep = explode
    with pytest.raises(RuntimeError, match="boom"):
        world.start()

    record = world.store.latest()
    assert record.phase == "failed"
    assert record.error == {
        "phase": "running",
        "message": "boom",
        "kind": "transient",
        "cause": "error",
    }
    assert (world.store.dir_for(record.id) / "report.md").is_file()


def test_interrupt_cancels_children_and_marks_cancelled(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [("hang",)])

    async def interrupt(seconds):  # ruff: ignore[unused-async]
        raise asyncio.CancelledError

    world.sim.sleep = interrupt
    with pytest.raises(asyncio.CancelledError):
        world.start()

    assert world.store.latest().phase == "cancelled"


def test_cancel_without_driver_marks_cancelled(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [("hang",)])
    world.sim.kill_after = 2
    with pytest.raises(DriverKilled):
        world.start()
    oid = world.store.latest().id

    record = cancel_orchestrator(world.repo, oid)

    assert record.phase == "cancelled"
    assert world.store.cancel_marker(oid).exists()
    assert cancel_orchestrator(world.repo, oid).phase == "cancelled"
    with pytest.raises(ValueError, match="cannot be resumed"):
        world.resume(oid)


def test_cancel_with_a_live_driver_leaves_the_stop_to_it(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [("hang",)])
    world.sim.kill_after = 2
    with pytest.raises(DriverKilled):
        world.start()
    oid = world.store.latest().id
    lock = DriverLock(world.store.dir_for(oid), mode="cli")
    assert lock.acquire()

    record = cancel_orchestrator(world.repo, oid)

    assert record.phase == "running"
    assert world.store.cancel_marker(oid).exists()
    lock.release()


def test_resume_rejects_task_and_request_when_not_blocked(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    oid, _code = world.start(dry_run=True)

    with pytest.raises(ValueError, match="only to a blocked"):
        world.resume(oid, task_id="api")
    with pytest.raises(ValueError, match="only to a blocked"):
        world.resume(oid, request="x")


def test_resume_of_a_dry_run_runs_the_tasks(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    oid, _code = world.start(dry_run=True)

    assert world.resume(oid) == 0

    record = world.record(oid)
    assert record.phase == "delivered"
    assert record.intake["dry_run"] is False


def test_resume_after_a_failed_decomposition_decomposes_again(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    world.script.decomposer_error = RuntimeError("model unavailable")

    oid, code = world.start()

    failed = world.record(oid)
    assert code == EXIT_FAILED
    assert failed.error == {
        "phase": "decomposing",
        "message": "model unavailable",
        "kind": "transient",
        "cause": "error",
    }
    world.script.decomposer_error = None

    assert world.resume(oid) == 0

    record = world.record(oid)
    assert record.phase == "delivered"
    assert [t.id for t in record.tasks] == ["api"]


def test_tasks_file_source_skips_the_decomposer(tmp_path, monkeypatch):
    world = one_task_world(tmp_path, monkeypatch, [PASS_API])
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text(
        '[[tasks]]\nid = "api"\nrequest = "Build api"\n', encoding="utf-8"
    )

    oid, code = world.start(intake=IntakeRequest("tasks", tasks_file=tasks_file))

    record = world.record(oid)
    assert code == 0
    assert record.objective == "Run the tasks in tasks.toml"
    assert world.script.decomposer_calls == []
    assert (
        world.script.pull_request.calls[0][0]
        == "Orchestrator: Run the tasks in tasks.toml"
    )


def test_jira_source_keeps_fetched_requests_and_titles_the_pr_with_keys(
    tmp_path, monkeypatch
):
    script = AgentScript([
        {
            "id": "PROJ-1",
            "request": "ignored",
            "source_ref": "PROJ-1",
            "depends_on": [],
        }
    ])
    world = World(tmp_path, monkeypatch, script, {"PROJ-1": [PASS_API]})
    issue = {"key": "PROJ-1", "summary": "Fix login", "description": "Steps"}

    async def fetch(repo, config, request, agents):  # ruff: ignore[unused-async]
        return [jira_task(issue)]

    monkeypatch.setattr(runner_module, "fetch_jira_tasks", fetch)

    oid, code = world.start(intake=IntakeRequest("jira", jira_keys=("PROJ-1",)))

    record = world.record(oid)
    assert code == 0
    assert record.source == "jira"
    assert record.objective == "Resolve Jira issues: PROJ-1"
    assert record.intake["jira_keys"] == ["PROJ-1"]
    assert (
        record.task("PROJ-1").request == "Resolve Jira issue PROJ-1: Fix login\n\nSteps"
    )
    assert script.pull_request.calls[0][0] == "Orchestrator: PROJ-1"
