"""A scripted native session: only `meow native orchestrate` helpers drive it."""

import json
import subprocess
from pathlib import Path

from meow.execution.orchestration.store import OrchestratorStore
from meow.native import native_orchestrate as orch
from tests.orchestration.conftest import complete_child


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def write_conflicting_tasks(directory: Path) -> Path:
    path = directory / "tasks.json"
    path.write_text(
        json.dumps({
            "tasks": [
                {"id": "a", "request": "Write shared.txt as a"},
                {"id": "b", "request": "Write shared.txt as b"},
            ]
        }),
        encoding="utf-8",
    )
    return path


def commit_project_config(repo: Path) -> None:
    """A real project carries `.meow/config.toml`; the final checks need one."""
    (repo / ".meow").mkdir(exist_ok=True)
    (repo / ".meow" / "config.toml").write_text("", encoding="utf-8")
    git(repo, "add", ".meow/config.toml")
    git(repo, "commit", "-qm", "Add MEOW config")


def run_children(repo: Path, store: OrchestratorStore, orch_id: str) -> None:
    first = orch.tick(repo, orch_id)
    assert (first["state"], first["launched"]) == ("waiting", ["a", "b"])
    for task_id in ("a", "b"):
        run_id = store.load(orch_id).task(task_id).run_ids[-1]
        complete_child(repo, run_id, {"shared.txt": f"from {task_id}\n"})


def resolve_conflict(repo: Path, store: OrchestratorStore, orch_id: str) -> None:
    second = orch.tick(repo, orch_id)
    assert second["state"] == "conflicts"
    conflict = second["conflicts"][0]
    assert (conflict["task"], conflict["paths"]) == ("b", ["shared.txt"])
    worktree = Path(store.load(orch_id).integration["worktree"])
    (worktree / "shared.txt").write_text("from a and b\n", encoding="utf-8")
    assert orch.merge_done(repo, orch_id, "b")["status"] == "merged"
    assert orch.tick(repo, orch_id)["state"] == "all_merged"


def pass_the_review(repo: Path, orch_id: str) -> None:
    verdict = Path(orch.review(repo, orch_id)["review_file"])
    verdict.parent.mkdir(parents=True, exist_ok=True)
    verdict.write_text("SUMMARY: fine\nSTATUS: PASS\n", encoding="utf-8")
    assert orch.review_result(repo, orch_id, verdict)["phase"] == "delivering"


def test_scripted_native_loop_delivers(git_repo, fake_launcher, monkeypatch):
    repo, remote = git_repo
    commit_project_config(repo)
    monkeypatch.setattr(orch, "LAUNCHER", fake_launcher)
    orch_id = orch.init(repo, tasks_file=write_conflicting_tasks(repo.parent))["id"]
    store = OrchestratorStore(repo)

    run_children(repo, store, orch_id)
    resolve_conflict(repo, store, orch_id)
    pass_the_review(repo, orch_id)
    delivery = orch.deliver(repo, orch_id)
    finished = orch.record_pr(repo, orch_id, url="https://example.test/pr/1")

    assert delivery["branch"] == f"orchestrator/{orch_id}"
    assert finished["exit_code"] == 0
    record = store.load(orch_id)
    assert record.phase == "delivered"
    assert record.delivery["pr"]["url"] == "https://example.test/pr/1"
    assert git(remote, "show", f"{delivery['branch']}:shared.txt") == "from a and b"
