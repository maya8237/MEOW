from .core import (
    _MISUSE_CHECKS as _MISUSE_CHECKS,
)
from .core import (
    CiReviewError as CiReviewError,
)
from .core import (
    DirtyWorkingTreeError as DirtyWorkingTreeError,
)
from .core import (
    EvidenceDocumentWriter as EvidenceDocumentWriter,
)
from .core import (
    IssueUnresolvedError as IssueUnresolvedError,
)
from .core import (
    Path as Path,
)
from .core import (
    PlanNotApprovedError as PlanNotApprovedError,
)
from .core import (
    _add_common_args as _add_common_args,
)
from .core import (
    _add_feature_args as _add_feature_args,
)
from .core import (
    _add_manual_approval_arg as _add_manual_approval_arg,
)
from .core import (
    _add_plan_parser as _add_plan_parser,
)
from .core import (
    _add_review_parser as _add_review_parser,
)
from .core import (
    _add_run_parser as _add_run_parser,
)
from .core import (
    _boot_repo as _boot_repo,
)
from .core import (
    _build_arg_parser as _build_arg_parser,
)
from .core import (
    _check_clean_tree as _check_clean_tree,
)
from .core import (
    _creates_a_worktree as _creates_a_worktree,
)
from .core import (
    _dispatch as _dispatch,
)
from .core import (
    _dispatch_jira_build as _dispatch_jira_build,
)
from .core import (
    _dispatch_lint_fix as _dispatch_lint_fix,
)
from .core import (
    _dispatch_plain_build as _dispatch_plain_build,
)
from .core import (
    _dispatch_review as _dispatch_review,
)
from .core import (
    _dispatch_run as _dispatch_run,
)
from .core import (
    _ensure_clean_tree as _ensure_clean_tree,
)
from .core import (
    _is_linked_worktree as _is_linked_worktree,
)
from .core import (
    _is_plain_build as _is_plain_build,
)
from .core import (
    _misused_flags as _misused_flags,
)
from .core import (
    _print_plan as _print_plan,
)
from .core import (
    _prompt_plan_approval as _prompt_plan_approval,
)
from .core import (
    _requires_clean_tree as _requires_clean_tree,
)
from .core import (
    _resolve_input_path as _resolve_input_path,
)
from .core import (
    _should_use_worktree as _should_use_worktree,
)
from .core import (
    _validate_build_flags as _validate_build_flags,
)
from .core import (
    _validate_ci_flags as _validate_ci_flags,
)
from .core import (
    _validate_feature_name_requirement as _validate_feature_name_requirement,
)
from .core import (
    _validate_lint_fix_flags as _validate_lint_fix_flags,
)
from .core import (
    _validate_run_flags as _validate_run_flags,
)
from .core import (
    add_native_parser as add_native_parser,
)
from .core import (
    argparse as argparse,
)
from .core import (
    assess_request as assess_request,
)
from .core import (
    asyncio as asyncio,
)
from .core import (
    audit_project as audit_project,
)
from .core import (
    cli_main as cli_main,
)
from .core import (
    configure_logging as configure_logging,
)
from .core import (
    create_selected_documents as create_selected_documents,
)
from .core import (
    get_logger as get_logger,
)
from .core import (
    json as json,
)
from .core import (
    load_config as load_config,
)
from .core import (
    load_shape_artifact as load_shape_artifact,
)
from .core import (
    log_working_directory as log_working_directory,
)
from .core import (
    logger as logger,
)
from .core import (
    os as os,
)
from .core import (
    reflect_breadboard as reflect_breadboard,
)
from .core import (
    resume as resume,
)
from .core import (
    run_ci_review as run_ci_review,
)
from .core import (
    run_issue_solver as run_issue_solver,
)
from .core import (
    run_lint_fix as run_lint_fix,
)
from .core import (
    run_native as run_native,
)
from .core import (
    run_plan as run_plan,
)
from .core import (
    run_review_command as run_review_command,
)
from .core import (
    run_sprint as run_sprint,
)
from builtins import input as input
_ORIGINAL_PROMPT_PLAN_APPROVAL = _prompt_plan_approval


def _prompt_plan_approval(plan_file):
    """Facade wrapper that keeps the public input patch point working."""
    import builtins
    from . import core as _core

    _core.input = input if input is not builtins.input else builtins.input
    return _ORIGINAL_PROMPT_PLAN_APPROVAL(plan_file)


def cli_main(*args, **kwargs):
    """Run the CLI while keeping the package facade patchable.

    The public ``meow.cli`` module re-exports the command dependencies.  Sync
    the core module's references at call time so callers patching those public
    names (as supported by the existing API and tests) affect dispatch too.
    """
    from . import core as _core

    _core.run_ci_review = run_ci_review
    _core._boot_repo = _boot_repo
    _core._prompt_plan_approval = _prompt_plan_approval
    _core.run_review_command = run_review_command
    _core.run_plan = run_plan
    _core.run_sprint = run_sprint
    _core.run_lint_fix = run_lint_fix
    _core.load_config = load_config
    _core._ensure_clean_tree = _ensure_clean_tree
    _core._is_linked_worktree = _is_linked_worktree
    _core.run_issue_solver = run_issue_solver
    import importlib

    _orchestrator_core = importlib.import_module("meow.orchestrator.core")
    _orchestrator_core.logger = __import__("meow.orchestrator", fromlist=["logger"]).logger
    return _core.cli_main(*args, **kwargs)
from .core import (
    select_findings as select_findings,
)
from .core import (
    shape_create as shape_create,
)
from .core import (
    status as status,
)
from .core import (
    structural_check as structural_check,
)
from .core import (
    sys as sys,
)
