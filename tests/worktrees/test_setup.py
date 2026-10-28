"""Explicit worktree setup must stay within its selected files."""

import subprocess

import pytest

from meow.infrastructure.worktree_setup import (
    WorktreeSetupError,
    copy_setup_file,
    run_setup,
    validate_setup,
)


def test_copy_selected_regular_file(tmp_path):
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    (repo / "local.cfg").write_text("value", encoding="utf-8")
    actions = []
    run_setup(
        repo,
        worktree,
        validate_setup({"copy": ["local.cfg"]}),
        record_action=actions.append,
    )
    assert (worktree / "local.cfg").read_text(encoding="utf-8") == "value"
    assert actions[0]["status"] == "copied"


@pytest.mark.parametrize("path", ["../outside", ".env", "secrets.toml"])
def test_rejects_unsafe_copy_path(path):
    with pytest.raises(ValueError):
        validate_setup({"copy": [path]})


def test_rejects_symlink_source_and_collision(tmp_path):
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    source = repo / "source.cfg"
    source.write_text("value", encoding="utf-8")
    try:
        (repo / "link.cfg").symlink_to(source)
    except OSError:
        pass  # Windows may deny symlink creation without developer mode.
    else:
        with pytest.raises(WorktreeSetupError):
            run_setup(repo, worktree, validate_setup({"copy": ["link.cfg"]}))


def test_policy_denies_setup_command_before_execution(tmp_path):
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    manifest = validate_setup({"commands": [["python", "-c", "print('ran')"]]})
    with pytest.raises(WorktreeSetupError, match="denied"):
        run_setup(
            repo,
            worktree,
            manifest,
            command_decision=lambda _argv: "deny",
        )


def test_setup_can_be_rerun_after_a_later_command_failed(tmp_path):
    """Resume re-applies setup; a copy that already landed is not a collision."""
    import sys

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    (repo / "local.cfg").write_text("value", encoding="utf-8")
    flag = worktree / "ready"
    code = f"import pathlib,sys; sys.exit(0 if pathlib.Path(r'{flag}').exists() else 1)"
    manifest = validate_setup({
        "copy": ["local.cfg"],
        "commands": [[sys.executable, "-c", code]],
    })
    with pytest.raises(WorktreeSetupError, match="exit 1"):
        run_setup(repo, worktree, manifest)
    flag.write_text("", encoding="utf-8")  # the transient cause is fixed
    run_setup(repo, worktree, manifest)  # must not trip over the copied file
    assert (worktree / "local.cfg").read_text(encoding="utf-8") == "value"


def _orphan_script(marker):
    child = f"import time;time.sleep(1.5);open(r'{marker}','w').close()"
    return (
        "import subprocess, sys, time;"
        f"subprocess.Popen([sys.executable, '-c', {child!r}]);"
        "time.sleep(60)"
    )


def test_timed_out_setup_command_leaves_no_orphans(tmp_path, monkeypatch):
    import sys
    import time

    from meow.infrastructure import worktree_setup

    monkeypatch.setattr(worktree_setup, "_COMMAND_TIMEOUT", 0.5)
    marker = tmp_path / "survivor"
    manifest = validate_setup({
        "commands": [[sys.executable, "-c", _orphan_script(marker)]]
    })
    with pytest.raises(WorktreeSetupError, match="timed out"):
        run_setup(tmp_path, tmp_path, manifest)
    time.sleep(2.5)
    assert not marker.exists()


def _git_worktree(path):
    path.mkdir()
    for argv in (
        ["init", "-q"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *argv], cwd=path, check=True)
    return path


def test_copy_identical_destination_is_skipped(tmp_path):
    source, dest = tmp_path / "s", tmp_path / "wt" / "d"
    source.write_text("v", encoding="utf-8")
    dest.parent.mkdir()
    dest.write_text("v", encoding="utf-8")
    assert copy_setup_file(source, dest, tmp_path / "wt") == "identical"


def test_copy_keeps_tracked_differing_destination(tmp_path):
    repo, worktree = tmp_path / "repo", _git_worktree(tmp_path / "wt")
    (repo / "backends").mkdir(parents=True)
    (repo / "backends/bun.lock").write_text("local", encoding="utf-8")
    (worktree / "backends").mkdir()
    (worktree / "backends/bun.lock").write_text("branch", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=worktree, check=True)
    actions = []
    run_setup(
        repo,
        worktree,
        validate_setup({"copy": ["backends/bun.lock"]}),
        record_action=actions.append,
    )
    assert (worktree / "backends/bun.lock").read_text(encoding="utf-8") == "branch"
    assert actions[0] == {"kind": "copy", "path": "backends/bun.lock", "status": "kept"}


def test_copy_replaces_untracked_with_backup(tmp_path):
    repo, worktree = tmp_path / "repo", _git_worktree(tmp_path / "wt")
    repo.mkdir()
    (repo / "local.cfg").write_text("new", encoding="utf-8")
    (worktree / "local.cfg").write_text("mine", encoding="utf-8")
    actions = []
    run_setup(
        repo,
        worktree,
        validate_setup({"copy": ["local.cfg"]}),
        record_action=actions.append,
    )
    assert (worktree / "local.cfg").read_text(encoding="utf-8") == "new"
    backup = worktree / ".meow/setup-backup/local.cfg"
    assert backup.read_text(encoding="utf-8") == "mine"
    assert actions[0]["status"] == "replaced"


def test_copy_missing_source_skips(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    assert copy_setup_file(tmp_path / "absent", worktree / "d", worktree) == "skipped"
    assert not (worktree / "d").exists()


def test_copy_onto_directory_still_errors(tmp_path):
    source, worktree = tmp_path / "s", tmp_path / "wt"
    source.write_text("v", encoding="utf-8")
    (worktree / "d").mkdir(parents=True)
    with pytest.raises(WorktreeSetupError):
        copy_setup_file(source, worktree / "d", worktree)
