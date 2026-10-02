"""Import and compatibility checks for the responsibility packages."""

from pathlib import Path

from meow import branch_reviewer, native, native_cli
from meow.execution import orchestrator as execution_orchestrator
from meow.integrations import gitlab as integration_gitlab
from meow.review import command as review_command
from meow.testing import gates as testing_gates
from meow.worktrees import lifecycle as worktree_lifecycle

MAX_ROOT_FILES = 4


def test_responsibility_packages_expose_legacy_implementations():
    assert execution_orchestrator.review_then_test is not None
    assert review_command.run_review_command is not None
    assert integration_gitlab.__name__ == "meow.integrations.gitlab"
    assert testing_gates.configured_checks is not None
    assert worktree_lifecycle.__name__ == "meow.worktrees.lifecycle"


def test_native_implementation_has_legacy_facades():
    assert native.shape_create is not None
    assert native_cli.add_native_parser is not None
    assert branch_reviewer._sanitize is not None


def test_package_root_has_no_flat_module_sprawl():
    root = Path(__file__).parents[1] / "src" / "meow"
    free_files = {path.name for path in root.glob("*.py")}
    assert len(free_files) <= MAX_ROOT_FILES, sorted(free_files)


def test_test_suite_is_grouped_by_responsibility():
    root = Path(__file__).parent
    free_tests = {path.name for path in root.glob("test_*.py")}
    assert free_tests <= {"test_native.py", "test_package_architecture.py"}
