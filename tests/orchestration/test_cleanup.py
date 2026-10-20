"""`meow worktree clean ORCHESTRATOR_ID` removes only what is provably safe."""

import subprocess
from pathlib import Path

import pytest

from meow.execution.orchestration.cleanup import clean_orchestrator
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.infrastructure.worktree_controls import WorktreeSafetyError


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def init_repository(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "file.txt").write_text("before", encoding="utf-8")
    (repo / ".gitignore").write_text(
        ".meow/*\n!.meow/\n!.meow/config.toml\n", encoding="utf-8"
    )
    git(repo, "add", "file.txt", ".gitignore")
    git(repo, "commit", "-qm", "initial")
    git(repo, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def add_worktrees(repo: Path, orch_id: str) -> dict:
    """Integration and task worktrees with the task merged and pushed."""
    integration = f"orchestrator/{orch_id}"
    task_branch = f"orchestrator/{orch_id}-api"
    integration_wt = repo / ".worktrees" / f"orch-{orch_id}"
    task_wt = repo / ".worktrees" / f"orch-{orch_id}-api"
    git(repo, "worktree", "add", "-q", "-b", integration, str(integration_wt))
    git(repo, "worktree", "add", "-q", "-b", task_branch, str(task_wt), integration)
    (task_wt / "api.py").write_text("print('api')\n", encoding="utf-8")
    git(task_wt, "add", "api.py")
    git(task_wt, "commit", "-qm", "api")
    git(integration_wt, "merge", "-q", "--no-ff", "-m", "Merge api", task_branch)
    git(integration_wt, "push", "-q", "origin", integration)
    return {
        "integration": integration,
        "task_branch": task_branch,
        "integration_wt": integration_wt,
        "task_wt": task_wt,
        "pushed": git(integration_wt, "rev-parse", "HEAD"),
    }


@pytest.fixture
def delivered(tmp_path):
    """A delivered orchestrator: one merged task, integration branch pushed."""
    repo, remote = init_repository(tmp_path)
    store = OrchestratorStore(repo)
    created = store.create(
        objective="Add login",
        source="objective",
        base_branch=git(repo, "branch", "--show-current"),
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )
    made = add_worktrees(repo, created.id)

    def mutate(record):
        record.phase = "delivered"
        record.integration.update(
            branch=made["integration"],
            worktree=str(made["integration_wt"]),
            merged=["api"],
        )
        record.delivery["pushed_commit"] = made["pushed"]
        record.tasks = [
            OrchestratorTask(
                "api",
                "Build the API",
                status="merged",
                branch=made["task_branch"],
                worktree=str(made["task_wt"]),
            )
        ]

    store.update(created.id, mutate)
    return {"repo": repo, "remote": remote, "store": store, "id": created.id, **made}


def test_clean_removes_worktrees_and_keeps_branches(delivered):
    removed = clean_orchestrator(delivered["repo"], delivered["id"])

    assert removed == [
        delivered["task_wt"].resolve(),
        delivered["integration_wt"].resolve(),
    ]
    assert not delivered["task_wt"].exists()
    assert not delivered["integration_wt"].exists()
    for branch in (delivered["integration"], delivered["task_branch"]):
        assert git(delivered["repo"], "rev-parse", "--verify", branch)


def test_clean_is_idempotent_once_worktrees_are_gone(delivered):
    clean_orchestrator(delivered["repo"], delivered["id"])

    assert clean_orchestrator(delivered["repo"], delivered["id"]) == []


@pytest.mark.parametrize("phase", ["running", "blocked", "failed", "cancelled"])
def test_clean_requires_delivered(delivered, phase):
    delivered["store"].update(
        delivered["id"], lambda record: setattr(record, "phase", phase)
    )

    with pytest.raises(WorktreeSafetyError, match=f"is {phase}, not delivered"):
        clean_orchestrator(delivered["repo"], delivered["id"])

    assert delivered["task_wt"].exists()


def test_clean_refuses_a_dirty_worktree(delivered):
    (delivered["task_wt"] / "scratch.txt").write_text("wip", encoding="utf-8")

    with pytest.raises(WorktreeSafetyError, match="uncommitted or untracked"):
        clean_orchestrator(delivered["repo"], delivered["id"])

    assert delivered["task_wt"].exists()
    assert delivered["integration_wt"].exists()


def test_clean_refuses_when_origin_lacks_the_delivered_commit(delivered):
    git(
        delivered["remote"],
        "update-ref",
        "-d",
        f"refs/heads/{delivered['integration']}",
    )

    with pytest.raises(WorktreeSafetyError, match="origin does not hold"):
        clean_orchestrator(delivered["repo"], delivered["id"])

    assert delivered["integration_wt"].exists()


def test_clean_refuses_when_nothing_was_pushed(delivered):
    delivered["store"].update(
        delivered["id"],
        lambda record: record.delivery.update(pushed_commit=None),
    )

    with pytest.raises(WorktreeSafetyError, match="was never pushed"):
        clean_orchestrator(delivered["repo"], delivered["id"])


def test_clean_refuses_an_unmerged_task_branch(delivered):
    task_wt = delivered["task_wt"]
    (task_wt / "extra.py").write_text("extra\n", encoding="utf-8")
    git(task_wt, "add", "extra.py")
    git(task_wt, "commit", "-qm", "unmerged extra")

    with pytest.raises(WorktreeSafetyError, match="is not merged into"):
        clean_orchestrator(delivered["repo"], delivered["id"])

    assert task_wt.exists()


def test_clean_refuses_a_worktree_outside_the_worktree_root(delivered, tmp_path):
    outside = tmp_path / "elsewhere"
    delivered["store"].update(
        delivered["id"],
        lambda record: record.integration.update(worktree=str(outside)),
    )

    with pytest.raises(WorktreeSafetyError, match="outside the MEOW worktree root"):
        clean_orchestrator(delivered["repo"], delivered["id"])


def test_consolidate_keeps_integration_and_deletes_merged_task_branch(delivered):
    from meow.execution.orchestration.cleanup import consolidate_orchestrator

    report = consolidate_orchestrator(delivered["repo"], delivered["id"])
    assert report["kept"] == []
    assert delivered["integration_wt"].is_dir()
    assert not delivered["task_wt"].exists()
    branches = git(
        delivered["repo"], "branch", "--format=%(refname:short)"
    ).splitlines()
    assert delivered["integration"] in branches
    assert delivered["task_branch"] not in branches
    assert consolidate_orchestrator(delivered["repo"], delivered["id"])["removed"] == []


def test_result_can_still_be_removed_after_consolidation(delivered):
    from meow.execution.orchestration.cleanup import consolidate_orchestrator

    consolidate_orchestrator(delivered["repo"], delivered["id"])
    removed = clean_orchestrator(delivered["repo"], delivered["id"])
    assert removed == [delivered["integration_wt"].resolve()]


def test_consolidate_preserves_dirty_worker(delivered):
    from meow.execution.orchestration.cleanup import consolidate_orchestrator

    (delivered["task_wt"] / "scratch.txt").write_text("keep", encoding="utf-8")
    report = consolidate_orchestrator(delivered["repo"], delivered["id"])
    assert report["kept"]
    assert delivered["task_wt"].is_dir()


def test_consolidate_also_removes_nested_parallel_workers(delivered):
    from meow.execution.orchestration.cleanup import consolidate_orchestrator
    from meow.execution.run_state import RunStore

    repo = delivered["repo"]
    store = RunStore(repo)
    child = store.create(
        source="orchestrator",
        request="API",
        repo=repo,
        worktree=delivered["task_wt"],
        branch=delivered["task_branch"],
    )
    nested = repo / ".worktrees" / f"{child.id}-slice"
    commit = git(repo, "rev-parse", delivered["task_branch"])
    git(repo, "worktree", "add", "--detach", str(nested), commit)
    store.transition(
        child.id,
        "complete",
        results={
            "tasks": {
                "slice": {
                    "status": "complete",
                    "commit": commit,
                    "worktree": str(nested),
                },
            }
        },
    )
    delivered["store"].update(
        delivered["id"], lambda rec: rec.tasks[0].run_ids.append(child.id)
    )
    report = consolidate_orchestrator(repo, delivered["id"])
    assert not nested.exists()
    assert report["kept"] == []
    assert delivered["integration_wt"].is_dir()
