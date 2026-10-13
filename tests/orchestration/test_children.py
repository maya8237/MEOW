import os
from pathlib import Path

import pytest

from meow.execution.orchestration.children import (
    cancel_children,
    failure_evidence,
    finalize_passed,
    launch_attempt,
    poll_task,
)
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.run_state import RunStore
from meow.infrastructure.background import BackgroundError
from meow.infrastructure.cancellation import cancel_requested
from tests.orchestration.conftest import (
    ORCH_CONFIG,
    complete_child,
    fail_child,
    git,
    make_repo,
    new_orchestrator,
)


def api_task(**fields) -> OrchestratorTask:
    return OrchestratorTask("api", "Build the API", **fields)


def launch(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    repo, store, orch_id, task_id, launcher
):
    return launch_attempt(
        repo, store, orch_id, task_id, config=ORCH_CONFIG, launcher=launcher
    )


def task_worktree(repo: Path, orch_id: str, task_id: str) -> Path:
    return repo / ".worktrees" / f"orch-{orch_id}-{task_id}"


def test_attempt_branches_from_integration_tip(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()], with_branch=False)
    integration = repo / ".worktrees" / f"orch-{oid}"
    git(repo, "worktree", "add", "-b", f"orchestrator/{oid}", str(integration))
    (integration / "merged.txt").write_text("from a sibling", encoding="utf-8")
    git(integration, "add", "merged.txt")
    git(integration, "commit", "-qm", "sibling work")

    launch(repo, store, oid, "api", fake_launcher)

    worktree = task_worktree(repo, oid, "api")
    assert git(worktree, "rev-parse", "HEAD") == git(integration, "rev-parse", "HEAD")
    assert (worktree / "merged.txt").read_text(encoding="utf-8") == "from a sibling"


def test_integration_and_task_branches_coexist(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])

    launch(repo, store, oid, "api", fake_launcher)

    assert git(repo, "rev-parse", "--verify", f"orchestrator/{oid}")
    assert git(repo, "rev-parse", "--verify", f"orchestrator/{oid}-api")


def test_launch_records_the_attempt(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])

    run_id = launch(repo, store, oid, "api", fake_launcher)

    task = store.load(oid).task("api")
    assert run_id == fake_launcher.launches[0]["run_id"]
    assert (task.status, task.run_ids) == ("running", [run_id])
    assert task.branch == f"orchestrator/{oid}-api"
    assert task.worktree == str(task_worktree(repo, oid, "api"))
    seed = fake_launcher.launches[0]["seed"]
    assert (seed.source, seed.request, seed.branch) == (
        "orchestrator",
        "Build the API",
        f"orchestrator/{oid}-api",
    )
    assert seed.worktree == task_worktree(repo, oid, "api")


def test_saved_argv_names_task_and_resume_point(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])

    launch(repo, store, oid, "api", fake_launcher)

    assert fake_launcher.launches[0]["argv"] == [
        "_orchestrator-task",
        oid,
        "api",
        "--resume-at",
        "generate",
    ]


def test_test_option_adds_the_test_flag(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()], test=True)

    launch(repo, store, oid, "api", fake_launcher)

    assert fake_launcher.launches[0]["argv"][-1] == "--test"


def test_second_attempt_reuses_worktree_and_passes_plan(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    launch(repo, store, oid, "api", fake_launcher)
    plan = repo / ".meow" / "plans" / "api.md"

    def requeue(record):
        task = record.task("api")
        task.status = "pending"
        task.next_attempt = {"resume_at": "review", "plan_file": str(plan)}

    store.update(oid, requeue)

    first = store.load(oid).task("api").run_ids[0]
    second = launch(repo, store, oid, "api", fake_launcher)

    task = store.load(oid).task("api")
    assert fake_launcher.launches[1]["argv"] == [
        "_orchestrator-task",
        oid,
        "api",
        "--resume-at",
        "review",
        "--plan",
        str(plan),
        "--preexisting",
    ]
    assert task.run_ids == [first, second]
    assert (task.status, task.next_attempt) == ("running", None)
    assert fake_launcher.launches[1]["seed"].worktree == task_worktree(repo, oid, "api")


def test_launch_failure_records_failed_attempt(git_repo):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])

    def broken(repo, argv, *, seed):
        raise BackgroundError("no workers available")

    assert launch(repo, store, oid, "api", broken) == ""

    task = store.load(oid).task("api")
    assert (task.status, task.run_ids) == ("failed", [])
    assert task.history[-1]["cause"] == "launch-failed"
    assert task.history[-1]["action"] == "launch"
    assert task.history[-1]["rationale"] == "no workers available"


def test_only_pending_tasks_can_be_launched(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task(status="running")])

    with pytest.raises(ValueError, match="task api is running, not pending"):
        launch(repo, store, oid, "api", fake_launcher)


def test_launch_requires_an_integration_branch(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    store.update(oid, lambda record: record.integration.update(branch=None))

    with pytest.raises(ValueError, match="integration branch is not set"):
        launch(repo, store, oid, "api", fake_launcher)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("created", "running"),
        ("preparing", "running"),
        ("committed", "running"),
        ("complete", "passed"),
        ("delivered", "passed"),
        ("exhausted", "failed"),
        ("failed", "failed"),
        ("cancelled", "failed"),
        ("interrupted_mutation", "failed"),
    ],
)
def test_poll_maps_phases(git_repo, fake_launcher, phase, expected):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    RunStore(repo).transition(run_id, phase)

    assert poll_task(repo, store.load(oid).task("api")) == expected


def test_dead_worker_without_terminal_phase_is_failed(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    RunStore(repo).update_background(
        run_id, pid=os.getpid(), start_identity="another-process", state="running"
    )

    assert poll_task(repo, store.load(oid).task("api")) == "failed"
    assert RunStore(repo).load(run_id).phase == "interrupted_mutation"


def test_vanished_run_is_failed(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    (RunStore(repo).directory / f"{run_id}.json").unlink()

    assert poll_task(repo, store.load(oid).task("api")) == "failed"


def test_polling_a_task_without_runs_is_rejected(git_repo):
    repo, _ = git_repo

    with pytest.raises(ValueError, match="task api has no child run to poll"):
        poll_task(repo, api_task())


def test_completed_child_with_uncommitted_work_is_committed(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    complete_child(repo, run_id, {"api.py": "x = 1\n"}, commit=False)
    task = store.load(oid).task("api")
    worktree = task_worktree(repo, oid, "api")

    head = finalize_passed(repo, task)

    assert git(worktree, "status", "--porcelain") == ""
    assert git(worktree, "rev-parse", "HEAD") == head
    assert git(worktree, "show", "--name-only", "--format=", head) == "api.py"


def test_committed_child_is_not_committed_again(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    complete_child(repo, run_id, {"api.py": "x = 1\n"})
    worktree = task_worktree(repo, oid, "api")
    before = git(worktree, "rev-parse", "HEAD")

    assert finalize_passed(repo, store.load(oid).task("api")) == before


def test_child_without_changes_keeps_the_branch_start(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    complete_child(repo, run_id, {})

    head = finalize_passed(repo, store.load(oid).task("api"))

    assert head == git(repo, "rev-parse", f"orchestrator/{oid}")


def test_task_commit_excludes_meow_runtime_files(tmp_path, fake_launcher):
    repo, _ = make_repo(tmp_path, ".worktrees/\n")
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    complete_child(
        repo,
        run_id,
        {
            ".meow/runs/x.json": "{}",
            ".meow/plans/p.md": "plan",
            ".meow/orchestrators/y/state.json": "{}",
            "api.py": "x = 1\n",
        },
        commit=False,
    )

    head = finalize_passed(repo, store.load(oid).task("api"))

    worktree = task_worktree(repo, oid, "api")
    committed = git(worktree, "show", "--name-only", "--format=", head).split()
    assert committed == ["api.py"]


def test_finalize_requires_a_worktree():
    with pytest.raises(ValueError, match="task api has no worktree"):
        finalize_passed(Path("."), api_task())


def test_cancel_signals_only_live_children(git_repo, fake_launcher):
    repo, _ = git_repo
    tasks = [
        OrchestratorTask("a", "do a"),
        OrchestratorTask("b", "do b"),
        OrchestratorTask("c", "do c"),
        OrchestratorTask("d", "do d"),
    ]
    store, oid = new_orchestrator(repo, tasks)
    run_a = launch(repo, store, oid, "a", fake_launcher)
    run_b = launch(repo, store, oid, "b", fake_launcher)
    run_d = launch(repo, store, oid, "d", fake_launcher)
    complete_child(repo, run_d, {})

    signalled = cancel_children(repo, store.load(oid))

    runs = RunStore(repo)
    assert signalled == [run_a, run_b]
    assert cancel_requested(runs, run_a) and cancel_requested(runs, run_b)
    assert not cancel_requested(runs, run_d)


def test_evidence_lists_existing_files_only(git_repo, fake_launcher, tmp_path):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)
    plan = tmp_path / "plans" / "api.md"
    plan.parent.mkdir()
    review = tmp_path / "plans" / "api-review.md"
    review.write_text("SUMMARY: x\nSTATUS: FAIL\n", encoding="utf-8")
    log = tmp_path / "worker.log"
    log.write_text("boom", encoding="utf-8")
    runs = RunStore(repo)
    runs.update_background(run_id, log=str(log))
    runs.transition(run_id, "exhausted", plan_file=str(plan), review_file=str(review))
    journal = runs.directory / f"{run_id}.json"
    task = store.load(oid).task("api")

    missing = tmp_path / "integration-api-1.log"
    assert failure_evidence(repo, task, integration_log=missing) == [
        str(journal),
        str(log),
        str(review),
    ]

    tester = tmp_path / "plans" / "api-test.md"
    tester.write_text("tested", encoding="utf-8")
    missing.write_text("conflict", encoding="utf-8")
    assert failure_evidence(repo, task, integration_log=missing) == [
        str(journal),
        str(log),
        str(review),
        str(tester),
        str(missing),
    ]


def test_evidence_for_a_task_without_runs_is_empty():
    assert failure_evidence(Path("."), api_task()) == []


def test_fail_child_helper_marks_the_run_exhausted(git_repo, fake_launcher):
    repo, _ = git_repo
    store, oid = new_orchestrator(repo, [api_task()])
    run_id = launch(repo, store, oid, "api", fake_launcher)

    fail_child(repo, run_id, "gates failed")

    record = RunStore(repo).load(run_id)
    assert (record.phase, record.last_failure) == ("exhausted", "gates failed")
