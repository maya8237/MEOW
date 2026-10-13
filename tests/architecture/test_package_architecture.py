"""Architecture checks for the responsibility packages."""

from pathlib import Path

REMOVED_COMPATIBILITY_MODULES = {
    "native.py",
    "cli/core.py",
    "execution/runner.py",
    "execution/state.py",
    "infrastructure/testing_gates.py",
    "infrastructure/testing_runner.py",
    "infrastructure/worktrees_lifecycle.py",
    "integrations/gitlab.py",
    "integrations/jira.py",
    "review/branch.py",
    "review/command.py",
    "integrations/branch_reviewer.py",
}


def _project_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "src" / "meow").is_dir()
    )


def test_compatibility_modules_are_removed():
    root = _project_root() / "src" / "meow"
    assert [
        relative
        for relative in REMOVED_COMPATIBILITY_MODULES
        if (root / relative).exists()
    ] == []
