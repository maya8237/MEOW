"""Architecture checks for the responsibility packages."""

from pathlib import Path

from meow.cli import review_cli
from meow.execution import orchestrator as execution_orchestrator
from meow.infrastructure import checks, worktree
from meow.integrations import branch_reviewer, github_reviewer, gitlab_reviewer
from meow.native import native, native_cli

ALLOWED_ROOT_FILES = {
    "__init__.py",
    "evaluation.py",
    "frontend.py",
    "plan_state.py",
}
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
}


def test_responsibility_packages_expose_canonical_implementations():
    assert execution_orchestrator.review_then_test is not None
    assert review_cli.run_review_command is not None
    assert gitlab_reviewer._fetch_merge_request is not None
    assert github_reviewer._fetch_pull_request is not None
    assert checks.configured_checks is not None
    assert worktree._resolve_working_dir is not None


def test_native_implementation_is_canonical():
    assert native.shape_create is not None
    assert native_cli.add_native_parser is not None
    assert branch_reviewer._sanitize is not None


def test_compatibility_modules_are_removed():
    root = Path(__file__).parents[1] / "src" / "meow"
    assert [
        relative
        for relative in REMOVED_COMPATIBILITY_MODULES
        if (root / relative).exists()
    ] == []


def test_package_root_has_no_flat_module_sprawl():
    root = Path(__file__).parents[1] / "src" / "meow"
    free_files = {path.name for path in root.glob("*.py")}
    assert free_files <= ALLOWED_ROOT_FILES, sorted(free_files)


def test_test_suite_is_grouped_by_responsibility():
    root = Path(__file__).parent
    free_tests = {path.name for path in root.glob("test_*.py")}
    assert free_tests <= {
        "test_native.py",
        "test_package_architecture.py",
        "test_config.py",
        "test_logging.py",
        "test_plan_files.py",
        "test_shaping.py",
        "test_claude_marketplace.py",
        "test_meow_layout.py",
    }
