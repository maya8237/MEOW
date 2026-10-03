"""Worktree cleanup refuses any uncertain or unpublished state."""

import subprocess

import pytest

from meow.execution.run_state import RunStore
from meow.infrastructure.worktree_controls import (
    WorktreeSafetyError,
    clean_worktree,
    inspect_worktree,
)


def _git(directory, *args):
    subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )


@pytest.fixture
def completed_worktree(tmp_path):
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "README.md").write_text("first", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "HEAD")
    worktree = repo / ".worktrees" / "feature"
    worktree.parent.mkdir()
    _git(repo, "worktree", "add", "-b", "feature", str(worktree))
    _git(worktree, "push", "-u", "origin", "feature")
    store = RunStore(repo)
    record = store.create(
        source="prompt",
        request="change",
        repo=repo,
        worktree=worktree,
        branch="feature",
    )
    store.transition(record.id, "complete")
    return repo, worktree, record.id


def test_clean_completed_pushed_worktree(completed_worktree):
    repo, worktree, run_id = completed_worktree
    assert inspect_worktree(repo, run_id).safe_to_clean
    clean_worktree(repo, run_id)
    assert not worktree.exists()


def test_refuse_dirty_worktree(completed_worktree):
    repo, worktree, run_id = completed_worktree
    (worktree / "new.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(WorktreeSafetyError, match="uncommitted"):
        clean_worktree(repo, run_id)
    assert worktree.exists()


def test_refuse_unpushed_commit(completed_worktree):
    repo, worktree, run_id = completed_worktree
    (worktree / "new.txt").write_text("new", encoding="utf-8")
    _git(worktree, "add", "new.txt")
    _git(worktree, "commit", "-m", "local")
    with pytest.raises(WorktreeSafetyError, match="unpushed"):
        clean_worktree(repo, run_id)
    assert worktree.exists()


def test_refuse_active_or_outside_worktree(completed_worktree, tmp_path):
    repo, worktree, run_id = completed_worktree
    store = RunStore(repo)
    store.transition(run_id, "generator_started")
    with pytest.raises(WorktreeSafetyError, match="generator_started"):
        clean_worktree(repo, run_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.transition(run_id, "complete", worktree=str(outside))
    with pytest.raises(WorktreeSafetyError, match="outside"):
        clean_worktree(repo, run_id)
    assert worktree.exists() and outside.exists()
