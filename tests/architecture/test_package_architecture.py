"""Architecture checks for the responsibility packages."""

from pathlib import Path

from meow.cli import review_cli
from meow.execution import orchestrator as execution_orchestrator
from meow.infrastructure import checks, worktree
from meow.integrations import branch_reviewer, github_reviewer, gitlab_reviewer
from meow.native import native, native_cli

ALLOWED_ROOT_FILES = {
    "__init__.py",
    "__main__.py",
    "evaluation.py",
    "frontend.py",
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


def _project_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "src" / "meow").is_dir()
    )


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
    root = _project_root() / "src" / "meow"
    assert [
        relative
        for relative in REMOVED_COMPATIBILITY_MODULES
        if (root / relative).exists()
    ] == []


def test_package_root_has_no_flat_module_sprawl():
    root = _project_root() / "src" / "meow"
    free_files = {path.name for path in root.glob("*.py")}
    assert free_files <= ALLOWED_ROOT_FILES, sorted(free_files)


def test_test_suite_is_grouped_by_responsibility():
    root = _project_root() / "tests"
    assert sorted(path.name for path in root.glob("test_*.py")) == []


def test_test_directories_are_not_empty():
    root = _project_root() / "tests"
    empty = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        files = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix != ".pyc"
        ]
        if not files:
            empty.append(directory.relative_to(root).as_posix())
    assert empty == []


def test_source_packages_are_not_empty():
    root = _project_root() / "src" / "meow"
    empty = []
    for init_file in root.rglob("__init__.py"):
        implementation_files = [
            path
            for path in init_file.parent.glob("*.py")
            if path.name != "__init__.py"
        ]
        if not implementation_files:
            empty.append(init_file.parent.relative_to(root).as_posix())
    assert empty == []
