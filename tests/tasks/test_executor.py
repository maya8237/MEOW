"""Worker worktrees integrate before dependent work and stay recoverable."""

import asyncio
import subprocess

from meow.tasks.executor import execute_in_worktrees
from meow.tasks.model import TaskGraph, TaskSpec


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_parallel_workers_integrate_before_dependent_task(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    graph = TaskGraph((
        TaskSpec("api", (), ("api.txt",), ()),
        TaskSpec("ui", (), ("ui.txt",), ()),
        TaskSpec("combine", ("api", "ui"), ("combined.txt",), ()),
    ))

    async def implement(spec, worktree):  # ruff: ignore[unused-async]
        if spec.id == "combine":
            assert (worktree / "api.txt").read_text() == "api"
            assert (worktree / "ui.txt").read_text() == "ui"
        (
            worktree / f"{spec.id if spec.id != 'combine' else 'combined'}.txt"
        ).write_text(spec.id, encoding="utf-8")

    result = asyncio.run(
        execute_in_worktrees(repo, graph, "a" * 32, implement, max_parallel=2)
    )
    assert result.passed
    assert (repo / "combined.txt").read_text() == "combine"
    assert len(result.outcomes) == len(graph.tasks)
    assert all(
        (repo / ".worktrees" / f"{'a' * 32}-{task.id}").is_dir() for task in graph.tasks
    )


def test_out_of_scope_edit_stops_integration_and_preserves_worker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    original = _git(repo, "rev-parse", "HEAD")
    graph = TaskGraph((TaskSpec("api", (), ("src/api",), ()),))

    async def implement(_spec, worktree):  # ruff: ignore[unused-async]
        (worktree / "outside.txt").write_text("keep", encoding="utf-8")

    result = asyncio.run(
        execute_in_worktrees(repo, graph, "b" * 32, implement, max_parallel=1)
    )
    assert not result.passed
    assert _git(repo, "rev-parse", "HEAD") == original
    assert (repo / ".worktrees" / f"{'b' * 32}-api" / "outside.txt").is_file()


def test_prestart_cancellation_is_not_success(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    graph = TaskGraph((TaskSpec("api", (), ("src/api",), ()),))

    async def implement(_spec, _worktree):  # ruff: ignore[unused-async]
        raise AssertionError("cancelled run started a worker")

    result = asyncio.run(
        execute_in_worktrees(
            repo, graph, "c" * 32, implement, max_parallel=1, cancel=lambda: True
        )
    )
    assert not result.passed
    assert result.outcomes == ()
