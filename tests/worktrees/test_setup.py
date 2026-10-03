"""Explicit worktree setup must stay within its selected files."""

import pytest

from meow.worktree.setup import WorktreeSetupError, run_setup, validate_setup


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
