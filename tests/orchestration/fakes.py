"""Shared fakes for the orchestration tests: a real git repository with a
bare remote, a launcher that never spawns a process, and helpers that finish
child runs the way a real task worker would."""

import subprocess
from pathlib import Path

from meow.execution.orchestration.integration import ensure_integration_worktree
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import RunStore
from meow.infrastructure.background import RunSeed
from meow.project.config import load_config

ORCHESTRATOR_DEFAULTS = {
    "max_parallel": 3,
    "max_recovery_cycles": 3,
    "review_rounds": 3,
    "branch_prefix": "orchestrator/",
    "poll_seconds": 5,
    "open_pr": True,
}


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_repository(tmp_path: Path, config_text: str = "") -> tuple[Path, Path]:
    """A repo on branch `main` with one commit (including `.meow/config.toml`,
    empty unless `config_text` is given, so checks can run in its worktrees)
    and a bare `origin`."""
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        ".meow/*\n!.meow/\n!.meow/config.toml\n.worktrees/\n", encoding="utf-8"
    )
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.toml").write_text(config_text, encoding="utf-8")
    git(repo, "add", "README.md", ".gitignore", ".meow/config.toml")
    git(repo, "commit", "-qm", "initial")
    git(repo, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def orchestrator_config(repo: Path) -> dict:
    """The project config with the pinned `[orchestrator]` defaults."""
    return {**load_config(repo), "orchestrator": dict(ORCHESTRATOR_DEFAULTS)}


def create_orchestrator(
    repo: Path, tasks: list[OrchestratorTask]
) -> tuple[OrchestratorStore, str]:
    """A `running` CLI orchestrator on `main` with its integration worktree."""
    store = OrchestratorStore(repo)
    record = store.create(
        objective="Build it",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )
    record.tasks = tasks
    record.phase = "running"
    ensure_integration_worktree(repo, record, ORCHESTRATOR_DEFAULTS["branch_prefix"])
    store.save(record)
    return store, record.id


class ScriptedLauncher:
    """Stands in for `launch_background`: creates the child run record and
    remembers the launch, but never starts a process."""

    def __init__(self) -> None:
        self.launches: list[dict] = []
        self.fail_for: set[str] = set()

    def __call__(
        self, repo: Path, argv: list[str], *, seed: RunSeed | None = None
    ) -> str:
        task_id = argv[2]
        if task_id in self.fail_for:
            raise OSError(f"cannot start a worker for {task_id}")
        assert seed is not None
        record = RunStore(repo).create(
            source=seed.source,
            request=seed.request,
            repo=repo,
            worktree=seed.worktree,
            branch=seed.branch,
        )
        self.launches.append({
            "argv": list(argv),
            "task_id": task_id,
            "run_id": record.id,
            "worktree": Path(seed.worktree),
        })
        return record.id

    def run_ids(self, task_id: str) -> list[str]:
        return [item["run_id"] for item in self.launches if item["task_id"] == task_id]


def finish_child(repo: Path, run_id: str, files: dict[str, str] | None = None) -> None:
    """Write `files` into the child's worktree and mark the run `complete`."""
    store = RunStore(repo)
    worktree = Path(store.load(run_id).worktree)
    for name, content in (files or {}).items():
        target = worktree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    store.transition(run_id, "complete")


def fail_child(repo: Path, run_id: str, reason: str) -> None:
    RunStore(repo).transition(run_id, "failed", last_failure=reason)
