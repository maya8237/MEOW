"""Worker worktrees integrate before dependent work and stay recoverable."""

import asyncio
import subprocess
import sys
import time

import pytest

from meow.tasks import executor
from meow.tasks.executor import execute_in_worktrees
from meow.tasks.model import TaskGraph, TaskSpec
from meow.tasks.runner import verify_task

MIN_TICKS = 10


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


def _one_commit_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    return repo


def test_noop_worker_completes_without_replaying_its_base(tmp_path, monkeypatch):
    repo = _one_commit_repo(tmp_path)
    original = _git(repo, "rev-parse", "HEAD")
    graph = TaskGraph((
        TaskSpec("noop", (), ("unused.txt",), ()),
        TaskSpec("change", ("noop",), ("change.txt",), ()),
        TaskSpec("verify", ("change",), ("unused.txt",), ()),
    ))
    real = executor.integrate_results
    integrated = []

    def integrate(*args):
        integrated.extend(outcome.id for outcome in args[2])
        return real(*args)

    async def implement(spec, worktree):  # ruff: ignore[unused-async]
        if spec.id == "change":
            (worktree / "change.txt").write_text("done", encoding="utf-8")
        elif spec.id == "verify":
            assert (worktree / "change.txt").read_text() == "done"

    monkeypatch.setattr(executor, "integrate_results", integrate)
    result = asyncio.run(
        execute_in_worktrees(repo, graph, "e" * 32, implement, max_parallel=1)
    )
    assert result.passed, [outcome.error for outcome in result.outcomes]
    assert integrated == ["change"]
    assert result.revision != original
    assert _git(repo, "rev-list", "--count", f"{original}..HEAD") == "1"


@pytest.mark.parametrize("empty_commit", [False, True])
def test_verification_only_worker_records_note_without_integrating(
    tmp_path, empty_commit
):
    repo = _one_commit_repo(tmp_path)
    original = _git(repo, "rev-parse", "HEAD")
    spec = TaskSpec(
        "verify",
        (),
        ("unused.txt",),
        (f'"{sys.executable}" -c "print(123)"', f'"{sys.executable}" -c "print(456)"'),
    )

    async def implement(spec, worktree):
        evidence = await verify_task(spec, worktree)
        assert [item["exit_code"] for item in evidence] == [0, 0]
        # A clean worker can have moved HEAD without changing any files.
        if empty_commit:
            _git(worktree, "commit", "--allow-empty", "-m", "verification")

    result = asyncio.run(
        execute_in_worktrees(
            repo, TaskGraph((spec,)), "f" * 32, implement, max_parallel=1
        )
    )
    assert result.passed, [outcome.error for outcome in result.outcomes]
    assert result.revision == original
    assert result.outcomes[0].note == "Verification only; no file changes."


def test_clean_worker_with_failed_verification_does_not_complete(tmp_path):
    repo = _one_commit_repo(tmp_path)
    original = _git(repo, "rev-parse", "HEAD")
    spec = TaskSpec(
        "verify", (), ("unused.txt",), (f'"{sys.executable}" -c "exit(7)"',)
    )

    async def implement(spec, worktree):
        await verify_task(spec, worktree)

    result = asyncio.run(
        execute_in_worktrees(
            repo, TaskGraph((spec,)), "f" * 32, implement, max_parallel=1
        )
    )
    assert not result.passed
    assert "verification failed" in result.outcomes[0].error
    assert result.outcomes[0].note is None
    assert result.revision == original


def _ticks_while_executing(repo):
    graph = TaskGraph((
        TaskSpec("api", (), ("api.txt",), ()),
        TaskSpec("ui", (), ("ui.txt",), ()),
    ))

    async def implement(spec, worktree):  # ruff: ignore[unused-async]
        (worktree / f"{spec.id}.txt").write_text(spec.id, encoding="utf-8")

    async def main():
        ticks = []
        work = asyncio.ensure_future(
            execute_in_worktrees(repo, graph, "c" * 32, implement, max_parallel=2)
        )

        async def heartbeat():
            while not work.done():
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        beat = asyncio.ensure_future(heartbeat())
        result = await work
        await beat
        return result, ticks

    return asyncio.run(main())


def test_integration_does_not_block_the_event_loop(tmp_path, monkeypatch):
    repo = _one_commit_repo(tmp_path)
    real = executor.integrate_results

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(executor, "integrate_results", slow)
    result, ticks = _ticks_while_executing(repo)
    assert result.passed
    assert len(ticks) >= MIN_TICKS


def test_worktree_creation_does_not_block_the_event_loop(tmp_path, monkeypatch):
    repo = _one_commit_repo(tmp_path)
    real = subprocess.run

    def slow(cmd, *args, **kwargs):
        if "worktree" in cmd and "add" in cmd:
            time.sleep(0.3)
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(executor.subprocess, "run", slow)
    result, ticks = _ticks_while_executing(repo)
    assert result.passed
    assert len(ticks) >= MIN_TICKS


def test_worktree_add_calls_are_serialized(tmp_path, monkeypatch):
    repo = _one_commit_repo(tmp_path)
    real = subprocess.run
    active = 0
    peak = 0

    def counting(cmd, *args, **kwargs):
        nonlocal active, peak
        if "worktree" in cmd and "add" in cmd:
            active += 1
            peak = max(peak, active)
            time.sleep(0.2)
            try:
                return real(cmd, *args, **kwargs)
            finally:
                active -= 1
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(executor.subprocess, "run", counting)
    result, _ = _ticks_while_executing(repo)
    assert result.passed
    assert peak == 1


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    return repo


def test_ownership_accepts_planner_spellings_of_a_directory(tmp_path):
    """`src/`, `./src` and `src\\` own `src/a.py`; the graph validator already
    treats them as the same path, so the edit check must too."""
    repo = _repo(tmp_path)
    graph = TaskGraph((
        TaskSpec("one", (), ("src/",), ()),
        TaskSpec("two", (), ("./lib",), ()),
        TaskSpec("three", (), ("docs\\",), ()),
    ))

    async def implement(spec, worktree):  # ruff: ignore[unused-async]
        folder = {"one": "src", "two": "lib", "three": "docs"}[spec.id]
        (worktree / folder).mkdir()
        (worktree / folder / "a.txt").write_text(spec.id, encoding="utf-8")

    result = asyncio.run(
        execute_in_worktrees(repo, graph, "c" * 32, implement, max_parallel=1)
    )
    assert result.passed, [o.error for o in result.outcomes]


def test_ownership_accepts_non_ascii_file_names(tmp_path):
    """git quotes non-ASCII names in `--name-only`; they must still match."""
    repo = _repo(tmp_path)
    graph = TaskGraph((TaskSpec("one", (), ("src",), ()),))

    async def implement(_spec, worktree):  # ruff: ignore[unused-async]
        (worktree / "src").mkdir()
        (worktree / "src" / "café.txt").write_text("x", encoding="utf-8")

    result = asyncio.run(
        execute_in_worktrees(repo, graph, "d" * 32, implement, max_parallel=1)
    )
    assert result.passed, [o.error for o in result.outcomes]
