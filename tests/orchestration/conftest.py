"""Fixtures for orchestrator tests: a git repo, a bare origin, a fake launcher."""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from meow.execution.orchestration.model import OrchestratorTask, integration_branch
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import RunStore

GITIGNORE = ".meow/*\n!.meow/\n!.meow/config.toml\n.worktrees/\n"
ORCH_CONFIG = {
    "orchestrator": {
        "max_parallel": 3,
        "max_recovery_cycles": 3,
        "review_rounds": 3,
        "branch_prefix": "orchestrator/",
        "poll_seconds": 5,
        "open_pr": True,
    }
}


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_repo(tmp_path: Path, gitignore: str = GITIGNORE) -> tuple[Path, Path]:
    """A repository on `main` with one commit and a bare `origin` remote."""
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "file.txt").write_text("before", encoding="utf-8")
    (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
    git(repo, "add", "file.txt", ".gitignore")
    git(repo, "commit", "-qm", "initial")
    git(repo, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    return repo.resolve(), remote


@pytest.fixture(scope="session")
def _git_repo_template(tmp_path_factory) -> tuple[Path, Path]:
    """Build the default repository once per session; `git_repo` copies it.

    Each `git` call is a process spawn, which is slow on Windows runners, so
    copying files is much cheaper than replaying the setup for every test."""
    return make_repo(tmp_path_factory.mktemp("git-template"))


@pytest.fixture
def git_repo(
    tmp_path: Path, _git_repo_template: tuple[Path, Path]
) -> tuple[Path, Path]:
    template_repo, template_remote = _git_repo_template
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    shutil.copytree(template_repo, repo)
    shutil.copytree(template_remote, remote)
    config = repo / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8").replace(str(template_remote), str(remote)),
        encoding="utf-8",
    )
    return repo.resolve(), remote


def new_orchestrator(  # ruff: ignore[too-many-arguments]
    repo: Path,
    tasks: list[OrchestratorTask],
    *,
    phase: str = "running",
    max_parallel: int = 3,
    test: bool = False,
    with_branch: bool = True,
) -> tuple[OrchestratorStore, str]:
    """Journal a record holding `tasks`; with `with_branch`, also create its
    integration branch at the repository's HEAD."""
    store = OrchestratorStore(repo)
    record = store.create(
        objective="Build it",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=max_parallel,
        options={"test": test, "pr": True},
    )
    branch = integration_branch("orchestrator/", record.id)
    if with_branch:
        git(repo, "branch", branch)
    record.integration["branch"] = branch
    record.phase = phase
    record.tasks = list(tasks)
    store.save(record)
    return store, record.id


@dataclass
class FakeLauncher:
    """Stands in for `launch_background`: journals a child run, spawns nothing."""

    launches: list[dict] = field(default_factory=list)

    def __call__(self, repo: Path, argv: list[str], *, seed) -> str:
        run = RunStore(repo).create(
            source=seed.source,
            request=seed.request,
            repo=repo,
            worktree=seed.worktree,
            branch=seed.branch,
        )
        self.launches.append({"argv": list(argv), "seed": seed, "run_id": run.id})
        return run.id


@pytest.fixture
def fake_launcher() -> FakeLauncher:
    return FakeLauncher()


def complete_child(
    repo: Path, run_id: str, files: dict[str, str], commit: bool = True
) -> None:
    """Write `files` into the child's worktree, optionally commit them, and mark
    the run `complete`."""
    runs = RunStore(repo)
    worktree = Path(runs.load(run_id).worktree)
    for name, text in files.items():
        path = worktree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if commit and files:
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", f"child {run_id}")
    runs.transition(run_id, "complete")


def fail_child(repo: Path, run_id: str, reason: str) -> None:
    """Mark the child's run `exhausted` with `reason` as its last failure."""
    RunStore(repo).transition(run_id, "exhausted", last_failure=reason)
