"""`_ensure_branch_worktree` can start a new branch at a given commit-ish."""

import subprocess

import pytest

from meow.infrastructure.worktree import (
    _ensure_branch_worktree,
    _ensure_feature_worktree,
    resolve_start_point,
)
from tests.worktrees.test_worktree import git, make_repo


def rev(directory, ref):
    return subprocess.run(
        ["git", "rev-parse", ref],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def repo_with_base_branch_ahead(tmp_path):
    repo = make_repo(str(tmp_path))
    git(repo, "switch", "-q", "-c", "base")
    (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    git(repo, "add", "extra.txt")
    git(repo, "commit", "-q", "-m", "ahead")
    git(repo, "switch", "-q", "-")
    return repo


def test_new_branch_starts_at_start_point(tmp_path):
    repo = repo_with_base_branch_ahead(tmp_path)

    path = _ensure_branch_worktree(repo, "w", "feature", start_point="base")

    assert rev(path, "HEAD") == rev(repo, "base")
    assert rev(path, "HEAD") != rev(repo, "HEAD")
    assert (path / "extra.txt").is_file()


def test_new_branch_without_start_point_starts_at_head(tmp_path):
    repo = repo_with_base_branch_ahead(tmp_path)

    path = _ensure_branch_worktree(repo, "w", "feature")

    assert rev(path, "HEAD") == rev(repo, "HEAD")


def test_existing_branch_ignores_start_point(tmp_path):
    repo = repo_with_base_branch_ahead(tmp_path)
    git(repo, "branch", "feature")

    path = _ensure_branch_worktree(repo, "w", "feature", start_point="base")

    assert rev(path, "HEAD") == rev(repo, "feature")
    assert rev(path, "HEAD") != rev(repo, "base")


def test_unknown_start_point_fails_without_creating_the_branch(tmp_path):
    repo = repo_with_base_branch_ahead(tmp_path)

    with pytest.raises(
        RuntimeError, match=r"refs/heads/no-such-ref.*refs/remotes/origin/no-such-ref"
    ):
        _ensure_branch_worktree(repo, "w", "feature", start_point="no-such-ref")

    assert not (repo / ".worktrees" / "w").exists()
    missing = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "feature"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    assert missing.returncode != 0


def test_option_like_start_point_is_rejected(tmp_path):
    repo = repo_with_base_branch_ahead(tmp_path)

    with pytest.raises(RuntimeError, match="Invalid start point"):
        _ensure_branch_worktree(repo, "w", "feature", start_point="--detach")


def origin_clone(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    main = tmp_path / "main"
    main.mkdir()
    repo = make_repo(str(main))
    git(repo, "remote", "add", "origin", str(origin))
    return repo


def test_start_point_from_linked_worktree(tmp_path):
    repo = origin_clone(tmp_path)
    integ = _ensure_branch_worktree(repo, "integ", "orchestrator/abc")
    (integ / "t.txt").write_text("t\n", encoding="utf-8")
    git(integ, "add", "t.txt")
    git(integ, "commit", "-q", "-m", "integ")

    path = _ensure_branch_worktree(
        integ, "t1", "orchestrator/abc-t1", start_point="orchestrator/abc"
    )

    assert rev(path, "HEAD") == rev(repo, "orchestrator/abc")


def test_start_point_fetches_missing_remote_branch(tmp_path):
    repo = origin_clone(tmp_path)
    git(repo, "switch", "-q", "-c", "foundation")
    git(repo, "push", "-q", "origin", "foundation")
    sha = rev(repo, "foundation")
    git(repo, "switch", "-q", "-")
    git(repo, "branch", "-q", "-D", "foundation")
    git(repo, "update-ref", "-d", "refs/remotes/origin/foundation")

    assert resolve_start_point(repo, "foundation") == sha


def test_start_point_unknown_ref_lists_tried(tmp_path):
    repo = origin_clone(tmp_path)

    with pytest.raises(
        RuntimeError, match=r"refs/heads/nope.*refs/remotes/origin/nope"
    ):
        resolve_start_point(repo, "nope")


def test_detached_worktree_uses_resolved_source(tmp_path):
    repo = origin_clone(tmp_path)
    git(repo, "switch", "-q", "-c", "foundation")
    git(repo, "push", "-q", "origin", "foundation")
    sha = rev(repo, "foundation")
    git(repo, "switch", "-q", "-")
    git(repo, "branch", "-q", "-D", "foundation")

    path = _ensure_feature_worktree(repo, "f", source_branch="foundation")

    assert rev(path, "HEAD") == sha
