"""Verified runs deliver only when the caller opted into a safe context."""

import subprocess
from pathlib import Path

import pytest

from meow.execution.delivery import commit_worktree, deliver_verified_run, push_fix
from meow.execution.run_state import RunStore

MAX_HANG_SECONDS = 15


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, Path]:
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


def record_for(repo: Path, active: Path) -> tuple[RunStore, str]:
    store = RunStore(repo)
    record = store.create(
        source="prompt",
        request="feature",
        repo=repo,
        worktree=active,
        branch="detached",
    )
    store.transition(record.id, "checks_finished")
    return store, record.id


def test_separate_feature_worktree_commits_and_pushes(tmp_path):
    repo, remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    assert deliver_verified_run(store, run_id) is True

    record = store.load(run_id)
    assert record.delivery["branch"] == "meow/feature"
    assert record.delivery["commit"] == git(active, "rev-parse", "HEAD")
    assert (
        git(remote, "rev-parse", "refs/heads/meow/feature") == record.delivery["commit"]
    )


@pytest.mark.parametrize("source_flag", ["--from", "-b"])
@pytest.mark.parametrize("background", [False, True])
def test_source_branch_delivers_to_separate_branch(tmp_path, source_flag, background):
    from meow.cli import cli
    from meow.infrastructure.worktree import _ensure_feature_worktree

    repo, remote = repository(tmp_path)
    git(repo, "branch", "release/test")
    (repo / "file.txt").write_text("committed main changes", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-qm", "advance main beyond source")
    (repo / "file.txt").write_text("uncommitted main changes", encoding="utf-8")
    parser = cli._build_arg_parser()
    args = parser.parse_args(
        [
            "run",
            "feature",
            "--name",
            "feature",
            source_flag,
            "release/test",
        ]
        + (["--background"] if background else [])
    )
    cli._validate_run_flags(parser, args)
    active = _ensure_feature_worktree(
        repo, args.feature_name, source_branch=args.source_branch, fresh=True
    )
    assert (active / "file.txt").read_text(encoding="utf-8") == "before"
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    assert deliver_verified_run(store, run_id, commit=None) is True

    assert git(remote, "show", "meow/feature:file.txt") == "after"
    assert git(active, "rev-parse", "HEAD^") == git(repo, "rev-parse", "release/test")
    assert git(repo, "show", "release/test:file.txt") == "before"
    assert (repo / "file.txt").read_text(encoding="utf-8") == "uncommitted main changes"


def test_in_place_requires_explicit_commit_permission(tmp_path):
    repo, remote = repository(tmp_path)
    (repo / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    assert deliver_verified_run(store, run_id) is False
    assert git(repo, "status", "--porcelain") == "M file.txt"
    assert deliver_verified_run(store, run_id, commit=True) is True
    assert git(remote, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("linked", [False, True])
def test_no_commit_leaves_changes_even_in_existing_worktree(tmp_path, linked):
    repo, remote = repository(tmp_path)
    active = tmp_path / "existing" if linked else repo
    if linked:
        git(repo, "worktree", "add", "-b", "existing", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)
    before = git(active, "rev-parse", "HEAD")

    assert deliver_verified_run(store, run_id, commit=False) is False

    assert git(active, "rev-parse", "HEAD") == before
    assert git(active, "status", "--porcelain") == "M file.txt"
    assert git(remote, "for-each-ref") == ""


def test_in_place_commit_without_push_needs_no_remote(tmp_path):
    repo, _remote = repository(tmp_path)
    git(repo, "remote", "remove", "origin")
    (repo / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    assert deliver_verified_run(store, run_id, commit=True, push=False) is True

    record = store.load(run_id)
    assert record.phase == "committed"
    assert record.delivery["committed"] is True
    assert "pushed" not in record.delivery
    assert git(repo, "status", "--porcelain") == ""


def test_delivery_keeps_shared_config_outside_runtime_state(tmp_path):
    repo, remote = repository(tmp_path)
    meow = repo / ".meow"
    meow.mkdir()
    (meow / "config.toml").write_text("max_rounds = 4\n", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    assert deliver_verified_run(store, run_id, commit=True) is True

    tree = git(remote, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert ".meow/config.toml" in tree
    assert not any(path.startswith(".meow/runs/") for path in tree)


def test_missing_remote_preserves_verified_work(tmp_path):
    repo, _ = repository(tmp_path)
    git(repo, "remote", "remove", "origin")
    (repo / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    with pytest.raises(RuntimeError, match="origin"):
        deliver_verified_run(store, run_id, commit=True)
    record = store.load(run_id)
    assert record.phase == "delivery_failed"  # no remote: blocking, not parked
    assert record.results["failure"]["kind"] == "blocking"
    assert git(repo, "status", "--porcelain") == "M file.txt"


def test_background_rejects_non_build_and_interactive_modes():
    from meow.cli import cli

    parser = cli._build_arg_parser()
    for argv in (
        ["run", "--lint-fix", "--background"],
        ["run", "feature", "--background", "--manually-approve-plan"],
    ):
        with pytest.raises(SystemExit):
            cli._validate_run_flags(parser, parser.parse_args(argv))


def test_commit_message_lists_onboarded_files(tmp_path):
    repo, _remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)
    store.transition(
        run_id,
        "checks_finished",
        results={"onboarding": {"files": [".gitignore", ".meow/config.toml"]}},
    )

    deliver_verified_run(store, run_id)

    assert "Onboarded: .gitignore, .meow/config.toml" in git(
        active, "log", "-1", "--format=%b"
    )


def test_commit_message_has_no_onboarding_note_by_default(tmp_path):
    repo, _remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    deliver_verified_run(store, run_id)

    assert "Onboarded" not in git(active, "log", "-1", "--format=%b")


def test_delivery_commits_where_git_has_no_identity(tmp_path, monkeypatch):
    """A fresh CI container has no user.name/user.email configured."""
    repo, remote = repository(tmp_path)
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "none"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("NAME", "EMAIL"):
        for kind in ("AUTHOR", "COMMITTER"):
            monkeypatch.delenv(f"GIT_{kind}_{name}", raising=False)
    monkeypatch.delenv("EMAIL", raising=False)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    assert deliver_verified_run(store, run_id) is True

    assert git(remote, "rev-parse", "refs/heads/meow/feature")


def test_delivery_does_not_hang_on_a_stuck_push(tmp_path, monkeypatch):
    import time

    from meow.execution import delivery

    repo, _ = repository(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    hook.chmod(0o755)
    monkeypatch.setattr(delivery, "_GIT_TIMEOUT", 1)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        deliver_verified_run(store, run_id)
    assert time.monotonic() - started < MAX_HANG_SECONDS
    record = store.load(run_id)  # a stuck push is transient: resumable
    assert record.phase == "parked"
    assert record.park["phase_before"] == "committed"
    assert record.results["next_command"].startswith(f"meow resume {run_id}")


def test_push_false_commits_without_pushing(tmp_path):
    repo, remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "-b", "task", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    assert deliver_verified_run(store, run_id, commit=True, push=False) is True

    record = store.load(run_id)
    assert record.phase == "committed"
    assert record.branch == "task"
    assert record.delivery["commit"] == git(active, "rev-parse", "HEAD")
    assert record.delivery["committed"] is True
    assert record.delivery["push"] is False
    assert "pushed" not in record.delivery
    assert git(remote, "for-each-ref") == ""
    assert git(active, "status", "--porcelain") == ""


def test_push_false_needs_no_origin(tmp_path):
    repo, _remote = repository(tmp_path)
    git(repo, "remote", "remove", "origin")
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "-b", "task", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    assert deliver_verified_run(store, run_id, commit=True, push=False) is True

    record = store.load(run_id)
    assert record.phase == "committed"
    assert record.delivery["commit"] == git(active, "rev-parse", "HEAD")


def test_push_false_on_detached_head_switches_to_run_branch(tmp_path):
    repo, _remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    deliver_verified_run(store, run_id, push=False)

    assert git(active, "branch", "--show-current") == "meow/feature"
    assert store.load(run_id).phase == "committed"


def test_default_delivery_still_pushes(tmp_path):
    repo, remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "-b", "task", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)

    assert deliver_verified_run(store, run_id, commit=True) is True

    record = store.load(run_id)
    assert record.phase == "delivered"
    assert record.delivery["pushed"] is True
    assert git(remote, "rev-parse", "refs/heads/task") == record.delivery["commit"]


def test_commit_worktree_excludes_meow_runtime_files(tmp_path):
    repo, _remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / ".meow" / "runs").mkdir(parents=True)
    (active / ".meow" / "runs" / "x.json").write_text("{}", encoding="utf-8")
    (active / ".meow" / "plans").mkdir()
    (active / ".meow" / "plans" / "p.md").write_text("plan", encoding="utf-8")
    (active / "src.py").write_text("x = 1\n", encoding="utf-8")

    commit = commit_worktree(active, ["-m", "Add src"])

    assert commit == git(active, "rev-parse", "HEAD")
    changed = git(active, "show", "--name-only", "--format=", "HEAD")
    assert changed.split() == ["src.py"]


def test_commit_worktree_keeps_shared_config_and_drops_secrets(tmp_path):
    repo, _remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / ".meow").mkdir()
    (active / ".meow" / "config.toml").write_text("max_rounds = 4\n", encoding="utf-8")
    (active / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    (active / "src.py").write_text("x = 1\n", encoding="utf-8")

    commit_worktree(active, ["-m", "Add src"])

    changed = git(active, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(changed) == [".meow/config.toml", "src.py"]


def test_commit_worktree_without_changes_returns_head(tmp_path):
    repo, _remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    before = git(active, "rev-parse", "HEAD")

    assert commit_worktree(active, ["-m", "Nothing"]) == before
    assert git(active, "rev-parse", "HEAD") == before


def test_commit_worktree_excludes_secrets_in_quoted_paths(tmp_path):
    repo, _ = repository(tmp_path)
    directory = repo / "café"
    directory.mkdir()
    (directory / ".env").write_text("TOKEN=fake\n", encoding="utf-8")
    (repo / "feature.py").write_text("x = 1\n", encoding="utf-8")

    commit_worktree(repo, ["-m", "feature"])

    names = git(repo, "ls-tree", "-rz", "--name-only", "HEAD").split("\0")
    assert "café/.env" not in names
    assert "feature.py" in names


@pytest.mark.parametrize("action", ["add", "edit", "delete"])
def test_commit_worktree_keeps_shared_project_definitions(tmp_path, action):
    from meow.project.onboarding import repair_boundary

    repo, _ = repository(tmp_path)
    repair_boundary(repo)
    relative = ".meow/workflows/feature.toml"
    path = repo / relative
    path.parent.mkdir(parents=True)
    if action != "add":
        path.write_text("before", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "definition")
    if action == "delete":
        path.unlink()
    else:
        path.write_text("after", encoding="utf-8")
    (repo / "feature.py").write_text("x = 1\n", encoding="utf-8")

    commit_worktree(repo, ["-m", "feature"])

    changed = git(repo, "diff", "HEAD~", "HEAD", "--name-only")
    assert relative in changed.splitlines()
    if action != "delete":
        assert git(repo, "show", f"HEAD:{relative}") == "after"


def delivered_worktree(tmp_path: Path) -> tuple[Path, Path, RunStore, str]:
    repo, remote = repository(tmp_path)
    active = tmp_path / "feature"
    git(repo, "worktree", "add", "--detach", str(active))
    (active / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, active)
    assert deliver_verified_run(store, run_id) is True
    return active, remote, store, run_id


def test_push_fix_returns_none_when_clean(tmp_path):
    _active, remote, store, run_id = delivered_worktree(tmp_path)
    before = git(remote, "rev-parse", "refs/heads/meow/feature")

    assert push_fix(store, run_id, "MEOW run x: pipeline fix round 1") is None
    assert git(remote, "rev-parse", "refs/heads/meow/feature") == before


def test_push_fix_commits_and_pushes_changes(tmp_path):
    active, remote, store, run_id = delivered_worktree(tmp_path)
    (active / "file.txt").write_text("fixed", encoding="utf-8")

    sha = push_fix(store, run_id, "MEOW run x: pipeline fix round 1")

    assert sha == git(active, "rev-parse", "HEAD")
    assert git(remote, "rev-parse", "refs/heads/meow/feature") == sha
    assert git(active, "log", "-1", "--format=%s") == "MEOW run x: pipeline fix round 1"
    assert store.load(run_id).delivery["commit"] != sha


@pytest.mark.parametrize("how", ["unpushed_fix", "fixer_commit"])
def test_push_fix_pushes_local_commit_ahead_of_remote(tmp_path, how):
    active, remote, store, run_id = delivered_worktree(tmp_path)
    (active / "file.txt").write_text(how, encoding="utf-8")
    git(active, "commit", "-qam", f"{how} commit")
    local = git(active, "rev-parse", "HEAD")

    sha = push_fix(store, run_id, "MEOW run x: pipeline fix round 1")

    assert sha == local
    assert git(remote, "rev-parse", "refs/heads/meow/feature") == local


def test_push_fix_refuses_a_switched_branch(tmp_path):
    active, remote, store, run_id = delivered_worktree(tmp_path)
    git(active, "switch", "-qc", "other")
    (active / "file.txt").write_text("fixed", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checked-out branch changed"):
        push_fix(store, run_id, "MEOW run x: pipeline fix round 1")
    assert git(remote, "branch", "--list", "other") == ""
    assert git(active, "status", "--porcelain")
