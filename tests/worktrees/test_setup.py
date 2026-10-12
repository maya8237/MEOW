"""Explicit worktree setup must stay within its selected files."""

import pytest

from meow.infrastructure.worktree_setup import (
    WorktreeSetupError,
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
    assert actions[0]["status"] == "passed"


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
    (worktree / "source.cfg").write_text("mine", encoding="utf-8")
    with pytest.raises(WorktreeSetupError):
        run_setup(repo, worktree, validate_setup({"copy": ["source.cfg"]}))


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
