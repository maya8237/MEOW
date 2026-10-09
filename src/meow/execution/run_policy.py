"""Which execution mode a run invocation selects, and whether it needs a clean tree."""

from pathlib import Path

from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import DirtyWorkingTreeError, _ensure_clean_tree

logger = get_logger(__name__)


def _is_plain_build(args) -> bool:
    """True for `run` with neither --lint-fix nor --jira -- the only mode
    that still plans+implements a brand new feature the original way."""
    return args.command == "run" and not args.lint_fix and args.jira is None


_MISUSE_CHECKS = (
    ("--name", lambda a: a.feature_name is not None),
    ("--source-branch", lambda a: a.source_branch is not None),
    ("--no-worktree", lambda a: a.no_worktree),
    ("--resume-at", lambda a: a.resume_at != "generate"),
    ("--plan/--plan-file/-p", lambda a: a.plan is not None),
)


def _misused_flags(args, checks) -> list[str]:
    """Names (as they appear on the CLI) of the given checks whose flag was
    actually set to a non-default, meaningful value -- used to reject a
    flag a mode would otherwise just silently ignore."""
    return [flag for flag, is_set in checks if is_set(args)]


def _should_use_worktree(args) -> bool:
    if args.command == "plan":
        return not args.no_worktree
    return _is_plain_build(args) and not args.no_worktree


def _requires_clean_tree(args) -> bool:
    """Plain build mode and --jira always edit in place or push a real
    branch; --lint-fix only does unless --report-only, which fixes nothing
    and is as read-only as `review` (which never requires a clean tree --
    its worktree/in-place-branch guards are its own concern, matching
    today's cr/review/gitlab-review/branch-review/review-fix-review).

    Plain build mode skips the check only when BOTH hold: a worktree is
    being created for this invocation (worktree mode, i.e. not
    --no-worktree) AND --source-branch was explicitly given for it -- the
    worktree is then built from that branch, not the main checkout's
    current state, so the main checkout's own uncommitted changes are
    irrelevant to it. Every other combination -- no worktree, or a
    worktree with no source branch -- keeps the check exactly as before.
    """
    if args.command != "run":
        return False
    if args.lint_fix:
        return not args.report_only
    if args.jira is not None:
        return True
    return not (not args.no_worktree and args.source_branch)


def _creates_a_worktree(args, *, use_worktree: bool) -> bool:
    """Every case where this invocation makes its own `.worktrees` entry --
    plain build mode (`use_worktree`), --jira's pushable branch, or
    `review --branch`'s isolated checkout -- so `.gitignore` needs the
    entry beforehand."""
    if use_worktree:
        return True
    if args.command == "run":
        return args.jira is not None
    if args.command == "review":
        return args.branch is not None and not args.no_worktree
    return False


def check_clean_tree(args, working_dir: Path) -> None:
    """Raise `DirtyWorkingTreeError` when this invocation edits the tree in
    place and the tree is dirty."""
    if not _requires_clean_tree(args):
        return
    try:
        _ensure_clean_tree(working_dir)
    except DirtyWorkingTreeError:
        logger.warning("run_blocked_uncommitted_changes")
        raise
    logger.info("run_initialized", command=args.command)
