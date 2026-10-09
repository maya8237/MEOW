"""Verified runs deliver only when the caller opted into a safe context."""

import subprocess
from pathlib import Path

import pytest

from meow.execution.delivery import deliver_verified_run
from meow.execution.run_state import RunStore


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
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
    assert record.delivery["branch"] == f"meow/{run_id}"
    assert record.delivery["commit"] == git(active, "rev-parse", "HEAD")
    assert (
        git(remote, "rev-parse", f"refs/heads/meow/{run_id}")
        == record.delivery["commit"]
    )


def test_in_place_requires_unattended(tmp_path):
    repo, remote = repository(tmp_path)
    (repo / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    assert deliver_verified_run(store, run_id) is False
    assert git(repo, "status", "--porcelain") == "M file.txt"
    assert deliver_verified_run(store, run_id, unattended=True) is True
    assert git(remote, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")


def test_delivery_keeps_shared_config_outside_runtime_state(tmp_path):
    repo, remote = repository(tmp_path)
    meow = repo / ".meow"
    meow.mkdir()
    (meow / "config.toml").write_text("max_rounds = 4\n", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    assert deliver_verified_run(store, run_id, unattended=True) is True

    tree = git(remote, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert ".meow/config.toml" in tree
    assert not any(path.startswith(".meow/runs/") for path in tree)


def test_missing_remote_preserves_verified_work(tmp_path):
    repo, _ = repository(tmp_path)
    git(repo, "remote", "remove", "origin")
    (repo / "file.txt").write_text("after", encoding="utf-8")
    store, run_id = record_for(repo, repo)

    with pytest.raises(RuntimeError, match="origin"):
        deliver_verified_run(store, run_id, unattended=True)
    assert store.load(run_id).phase == "delivery_failed"
    assert git(repo, "status", "--porcelain") == "M file.txt"


def test_unattended_flag_rejects_non_build_and_interactive_modes():
    from meow import cli

    parser = cli._build_arg_parser()
    for argv in (
        ["run", "--lint-fix", "--unattended"],
        ["run", "feature", "--unattended", "--manually-approve-plan"],
    ):
        with pytest.raises(SystemExit):
            cli._validate_run_flags(parser, parser.parse_args(argv))
