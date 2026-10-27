"""`_ensure_branch_worktree` can start a new branch at a given commit-ish."""

import subprocess
from types import SimpleNamespace

import pytest

import meow.infrastructure.worktree as worktree_module
from meow.infrastructure.worktree import (
    StartPointError,
    _ensure_branch_worktree,
    _ensure_feature_worktree,
    main_repo,
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


def linked_ahead_of_main(tmp_path):
    repo = origin_clone(tmp_path)
    integ = _ensure_branch_worktree(repo, "integ", "orchestrator/abc")
    (integ / "t.txt").write_text("t\n", encoding="utf-8")
    git(integ, "add", "t.txt")
    git(integ, "commit", "-q", "-m", "integ")
    assert rev(integ, "HEAD") != rev(repo, "HEAD")
    return repo, integ


@pytest.mark.parametrize("start_point", ["HEAD", None])
def test_head_and_no_ref_use_the_linked_worktrees_head(tmp_path, start_point):
    _repo, integ = linked_ahead_of_main(tmp_path)

    path = _ensure_branch_worktree(
        integ, "t2", "orchestrator/abc-t2", start_point=start_point
    )

    assert rev(path, "HEAD") == rev(integ, "HEAD")


def test_missing_ref_is_not_transient(tmp_path):
    repo = origin_clone(tmp_path)
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/other")

    with pytest.raises(StartPointError) as caught:
        resolve_start_point(repo, "nope")

    assert caught.value.ref == "nope"
    assert caught.value.fetch_returncode not in {None, 0}
    assert caught.value.transient is False


def test_bogus_origin_path_is_not_transient(tmp_path):
    repo = origin_clone(tmp_path)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "does-not-exist"))

    with pytest.raises(StartPointError) as caught:
        resolve_start_point(repo, "nope")

    assert caught.value.transient is False


def test_no_origin_remote_is_not_transient(tmp_path):
    repo = make_repo(str(tmp_path))

    with pytest.raises(StartPointError) as caught:
        resolve_start_point(repo, "nope")

    assert caught.value.transient is False


def test_network_fetch_failure_is_transient(tmp_path, monkeypatch):
    repo = origin_clone(tmp_path)
    real = worktree_module._run_git

    def fake(argv, **kwargs):
        if "fetch" in argv:
            return SimpleNamespace(
                returncode=128,
                stdout="",
                stderr="fatal: unable to access: Could not resolve host: x",
            )
        return real(argv, **kwargs)

    monkeypatch.setattr(worktree_module, "_run_git", fake)

    with pytest.raises(StartPointError, match=r"refs/heads/nope") as caught:
        resolve_start_point(repo, "nope")

    assert caught.value.transient is True
    assert "Could not resolve host" in caught.value.fetch_stderr


def test_fetch_timeout_is_transient(tmp_path, monkeypatch):
    repo = origin_clone(tmp_path)
    real = worktree_module._run_git

    def fake(argv, **kwargs):
        if "fetch" in argv:
            raise RuntimeError("git fetch timed out after 600s")
        return real(argv, **kwargs)

    monkeypatch.setattr(worktree_module, "_run_git", fake)

    with pytest.raises(StartPointError) as caught:
        resolve_start_point(repo, "nope")

    assert caught.value.transient is True


def test_worktree_add_failure_after_resolve_releases_claim(tmp_path, monkeypatch):
    repo = origin_clone(tmp_path)

    def boom(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="boom")

    monkeypatch.setattr(worktree_module.subprocess, "run", boom)

    with pytest.raises(RuntimeError, match=r"Failed to create worktree.*boom"):
        _ensure_feature_worktree(repo, "f", source_branch="HEAD", fresh=True)

    assert not (repo / ".worktrees" / "f").exists()


@pytest.mark.parametrize("form", ["tag", "full", "abbrev", "origin"])
def test_ref_forms_resolve(tmp_path, form):
    repo = origin_clone(tmp_path)
    git(repo, "tag", "v1")
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/x")
    git(repo, "fetch", "-q", "origin")
    sha = rev(repo, "HEAD")
    ref = {"tag": "v1", "full": sha, "abbrev": sha[:8], "origin": "origin/x"}[form]

    assert resolve_start_point(repo, ref) == sha


def test_detached_worktree_from_linked_worktree_context(tmp_path):
    repo = origin_clone(tmp_path)
    git(repo, "switch", "-q", "-c", "foundation")
    git(repo, "push", "-q", "origin", "foundation")
    sha = rev(repo, "foundation")
    git(repo, "switch", "-q", "-")
    git(repo, "branch", "-q", "-D", "foundation")
    linked = _ensure_branch_worktree(repo, "integ", "orchestrator/abc")

    assert resolve_start_point(linked, "foundation") == sha
    assert main_repo(linked) == repo.resolve()
