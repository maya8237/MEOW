"""Import and compatibility checks for the responsibility packages."""

from pathlib import Path

from meow import branch_reviewer, native, native_cli
from meow.execution import orchestrator as execution_orchestrator
from meow.infrastructure import testing_gates
from meow.infrastructure import worktrees_lifecycle as worktree_lifecycle
from meow.integrations import gitlab as integration_gitlab
from meow.review import command as review_command

# These are deliberate compatibility facades and package entry points.  The
# refactor keeps them at the package root so existing imports continue to work.
ALLOWED_ROOT_FILES = {
    "__init__.py",
    "evaluation.py",
    "frontend.py",
    "native.py",
    "plan_state.py",
}


def test_responsibility_packages_expose_legacy_implementations():
    assert execution_orchestrator.review_then_test is not None
    assert review_command.run_review_command is not None
    assert integration_gitlab.__name__ == "meow.integrations.gitlab"
    assert testing_gates.configured_checks is not None
    assert worktree_lifecycle.__name__ == "meow.infrastructure.worktrees_lifecycle"


def test_native_implementation_has_legacy_facades():
    assert native.shape_create is not None
    assert native_cli.add_native_parser is not None
    assert branch_reviewer._sanitize is not None


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
    }
