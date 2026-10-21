"""A child process runs one task attempt as an automatic, push-free sprint."""

import asyncio

from meow.cli.parser import _build_arg_parser
from meow.execution.orchestration import task_worker
from meow.execution.run_state import RunStore

ORCHESTRATOR_ID = "0123456789ab"


def seeded_run(tmp_path):
    worktree = tmp_path / ".worktrees" / f"orch-{ORCHESTRATOR_ID}-t1"
    worktree.mkdir(parents=True)
    record = RunStore(tmp_path).create(
        source="orchestrator",
        request="Build the api",
        repo=tmp_path,
        worktree=worktree,
        branch=f"orchestrator/{ORCHESTRATOR_ID}-t1",
    )
    return worktree, record.id


def capture_sprint(monkeypatch):
    captured = {}

    async def fake_run_sprint(  # ruff: ignore[unused-async]
        working_dir, feature, request, **kwargs
    ):
        captured.update(kwargs, working_dir=working_dir, feature=feature)
        captured["request"] = request

    monkeypatch.setattr(task_worker, "run_sprint", fake_run_sprint)
    return captured


def test_task_attempt_runs_sprint_without_push(tmp_path, monkeypatch):
    worktree, run_id = seeded_run(tmp_path)
    captured = capture_sprint(monkeypatch)
    args = _build_arg_parser().parse_args(["_orchestrator-task", ORCHESTRATOR_ID, "t1"])

    asyncio.run(task_worker.run_task_attempt(tmp_path, run_id, args))

    assert captured["working_dir"] == worktree.resolve()
    assert captured["feature"] == f"orch-{ORCHESTRATOR_ID}-t1"
    assert captured["request"] == "Build the api"
    assert captured["run_id"] == run_id
    assert captured["record_root"] == tmp_path
    assert captured["source"] == "orchestrator"
    assert captured["use_worktree"] is False
    assert captured["push"] is False
    assert captured["test"] is False
    assert captured["resume_at"] == "generate"
    assert captured["plan_file"] is None
    assert captured["worktree_preexisting"] is False


def test_task_attempt_forwards_resume_options(tmp_path, monkeypatch):
    _worktree, run_id = seeded_run(tmp_path)
    captured = capture_sprint(monkeypatch)
    args = _build_arg_parser().parse_args([
        "_orchestrator-task",
        ORCHESTRATOR_ID,
        "t1",
        "--resume-at",
        "review",
        "--plan",
        str(tmp_path / "plan.md"),
        "--test",
        "--preexisting",
    ])

    asyncio.run(task_worker.run_task_attempt(tmp_path, run_id, args))

    assert captured["resume_at"] == "review"
    assert captured["plan_file"] == tmp_path / "plan.md"
    assert captured["test"] is True
    assert captured["worktree_preexisting"] is True
