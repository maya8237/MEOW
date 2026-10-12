"""A worktree directory deleted behind git's back must not wedge the next run."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from meow.infrastructure import worktree
from meow.infrastructure.worktree import (
    _ensure_branch_worktree,
    _ensure_existing_branch_worktree,
    _ensure_feature_worktree,
    _push_branch,
)

MAX_HANG_SECONDS = 15


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_branch_worktree_is_recreated_after_its_directory_was_cleaned(repo):
    first = _ensure_branch_worktree(repo, "issue-1", "issue/1")
    shutil.rmtree(first)  # e.g. `git clean -fdx` in a persisted CI workspace
    again = _ensure_branch_worktree(repo, "issue-1", "issue/1")
    assert again.is_dir()
    assert git(again, "branch", "--show-current").strip() == "issue/1"


def test_existing_branch_worktree_is_recreated_after_its_directory_was_cleaned(repo):
    git(repo, "branch", "feature/x")
    first = _ensure_existing_branch_worktree(repo, "br-x", "feature/x")
    shutil.rmtree(first)
    again = _ensure_existing_branch_worktree(repo, "br-x", "feature/x")
    assert git(again, "branch", "--show-current").strip() == "feature/x"


def test_feature_worktree_is_recreated_after_its_directory_was_cleaned(repo):
    first = _ensure_feature_worktree(repo, "feat")
    shutil.rmtree(first)
    assert _ensure_feature_worktree(repo, "feat").is_dir()


def test_push_does_not_hang_on_a_stuck_remote(repo, tmp_path_factory, monkeypatch):
    remote = tmp_path_factory.mktemp("remote")
    git(remote, "init", "-q", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    hook.chmod(0o755)
    monkeypatch.setattr(worktree, "_GIT_TIMEOUT", 1)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        _push_branch(repo, "master")
    assert time.monotonic() - started < MAX_HANG_SECONDS
