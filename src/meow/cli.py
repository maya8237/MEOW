"""CLI entry point for the meow harness."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from meow.gitlab_reviewer import run_gitlab_review
from meow.issue_solver import IssueUnresolvedError, run_issue_solver
from meow.lint_fix import run_lint_fix
from meow.logging import configure_logging, get_logger
from meow.orchestrator import (
    PlanNotApprovedError,
    log_working_directory,
    run_plan,
    run_prompt_review,
    run_review,
    run_sprint,
)
from meow.worktree import DirtyWorkingTreeError, _boot_repo, _ensure_clean_tree

logger = get_logger(__name__)


def _add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--working-dir", "--work-dir", "-d",
        dest="working_dir",
        default=".",
        help="Working directory for the harness (default: current directory).",
    )


def _add_feature_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--name", "--feature-name", "-f", dest="feature_name", default=None,
        help=(
            "Feature name used for generated files and as the worktree name "
            "when worktrees are enabled. Required unless --no-worktree/-n "
            "is supplied."
        ),
    )
    parser.add_argument(
        "--no-worktree", "--noworktree", "-n",
        dest="no_worktree",
        action="store_true",
        help="Run in the main repo instead of creating/using a .worktrees entry.",
    )
    parser.add_argument(
        "--source-branch", "--from", "-b",
        dest="source_branch",
        default=None,
        help=(
            "Branch a freshly created worktree should be checked out from, "
            "instead of the main checkout's current HEAD. Ignored when an "
            "existing worktree is reused, or when --no-worktree is set "
            "(there is no worktree to create). Skips the uncommitted-"
            "changes check for this invocation, but only together with "
            "worktree mode (i.e. not --no-worktree) -- the new worktree is "
            "built from this branch, not the main checkout's current "
            "state, so its own uncommitted changes don't apply to it."
        ),
    )


def _add_manual_approval_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--manually-approve-plan", "-m",
        dest="manually_approve_plan",
        action="store_true",
        help=(
            "Show the plan after the planner writes it and ask for "
            "approval before the generator implements it. Declining exits "
            "without running the generator."
        ),
    )


def _print_plan(plan_file: Path) -> None:
    print(f"\n----- Sprint plan: {plan_file} -----\n")
    print(plan_file.read_text(encoding="utf-8"))
    print("----- end of plan -----\n")


def _prompt_plan_approval(plan_file: Path) -> bool:
    """Show the plan and ask the user to approve it before the generator
    runs -- the default `approve_plan` callback for --manually-approve-plan.
    EOF (no console attached to answer) is treated as declining, the same
    as any other unclear answer; only 'y'/'yes' approves."""
    _print_plan(plan_file)
    try:
        answer = input("Proceed with this plan? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _validate_feature_name_requirement(parser: argparse.ArgumentParser, args) -> None:
    requires_name = args.command in {"run", "plan"} and not args.no_worktree
    if requires_name and args.feature_name is None:
        parser.error(
            "--name/--feature-name/-f is required unless "
            "--no-worktree/-n is supplied"
        )


def _should_use_worktree(args) -> bool:
    return args.command in {"run", "plan"} and not args.no_worktree


def _resolve_input_path(path: str | None, working_dir: Path) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else working_dir / candidate).resolve()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_run_parser(subparsers)
    _add_plan_parser(subparsers)
    _add_review_parser(subparsers)
    _add_cr_parser(subparsers)
    _add_issue_parser(subparsers)
    _add_gitlab_review_parser(subparsers)
    _add_lint_fix_parser(subparsers)

    return parser


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    run_parser = subparsers.add_parser(
        "run", help="Plan, implement, and review a feature request end to end."
    )
    run_parser.add_argument("request", help="Feature request text.")
    run_parser.add_argument(
        "--plan", "--plan-file", "-p",
        dest="plan",
        default=None,
        help="Use an existing plan file instead of generating a new one.",
    )
    run_parser.add_argument(
        "--resume-at",
        dest="resume_at",
        choices=["generate", "review"],
        default="generate",
        help=(
            "Where to resume the round loop when a plan already exists "
            "(via --plan-file, or auto-detected in docs_dir when not "
            "given): 'generate' (default) starts with the generator, same "
            "as a fresh sprint. 'review' skips straight to reviewing the "
            "existing code first, and only runs the generator if the "
            "review finds something to fix -- for continuing a sprint "
            "that was interrupted after the generator already produced "
            "code, without re-running it on code that's already there."
        ),
    )
    _add_common_args(run_parser)
    _add_feature_args(run_parser)
    _add_manual_approval_arg(run_parser)


def _add_plan_parser(subparsers: argparse._SubParsersAction) -> None:
    plan_parser = subparsers.add_parser(
        "plan",
        help="Write a sprint plan for a feature request, without implementing it.",
    )
    plan_parser.add_argument("request", help="Feature request text.")
    _add_common_args(plan_parser)
    _add_feature_args(plan_parser)


def _add_review_parser(subparsers: argparse._SubParsersAction) -> None:
    review_parser = subparsers.add_parser(
        "review",
        help="Review an existing plan's implementation and fix any issues found.",
    )
    review_parser.add_argument(
        "--plan", "--plan-file", "-p",
        dest="plan",
        default=None,
        help="Plan file to review (default: latest plan in docs_dir).",
    )
    _add_common_args(review_parser)


def _add_cr_parser(subparsers: argparse._SubParsersAction) -> None:
    cr_parser = subparsers.add_parser(
        "cr",
        help=(
            "Review the current implementation against a free-text prompt, "
            "or the git diff when no prompt is provided."
        ),
    )
    cr_parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help=(
            "Description of the feature to review against. If omitted, "
            "the reviewer grades the current git diff."
        ),
    )
    _add_common_args(cr_parser)


def _add_issue_parser(subparsers: argparse._SubParsersAction) -> None:
    issue_parser = subparsers.add_parser(
        "issue",
        help=(
            "Fetch a Jira issue (or the latest one in the configured "
            "project) and solve it end to end in a pushed worktree branch."
        ),
    )
    issue_parser.add_argument(
        "issue",
        nargs="?",
        default=None,
        help=(
            "Jira issue key (e.g. PROJ-123). If omitted, uses the most "
            "recently created issue in [jira].project_key."
        ),
    )
    _add_common_args(issue_parser)
    _add_manual_approval_arg(issue_parser)


def _add_gitlab_review_parser(subparsers: argparse._SubParsersAction) -> None:
    gitlab_review_parser = subparsers.add_parser(
        "gitlab-review",
        help=(
            "Fetch a GitLab merge request's diff and grade it, reporting a "
            "PASS/FAIL verdict without editing anything."
        ),
    )
    gitlab_review_parser.add_argument(
        "mr_link", help="GitLab merge request URL to review."
    )
    _add_common_args(gitlab_review_parser)


def _add_lint_fix_parser(subparsers: argparse._SubParsersAction) -> None:
    lint_fix_parser = subparsers.add_parser(
        "lint-fix",
        help=(
            "Run every configured lint command and fix what it finds. "
            "--report-only runs and reports only, fixing nothing."
        ),
    )
    lint_fix_parser.add_argument(
        "--report-only",
        dest="report_only",
        action="store_true",
        help=(
            "Only run the configured lint commands and report failures -- "
            "apply no fixes and run no agent. Used by the lint-fix skill "
            "wrapper, which fixes what's reported itself."
        ),
    )
    _add_common_args(lint_fix_parser)


def _dispatch_issue(args, working_dir: Path) -> None:
    approve_plan = _prompt_plan_approval if args.manually_approve_plan else None
    try:
        result = asyncio.run(
            run_issue_solver(working_dir, args.issue, approve_plan=approve_plan)
        )
    except IssueUnresolvedError as exc:
        print(f"\nWARNING: could not resolve the issue: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result))


def _dispatch_gitlab_review(args, working_dir: Path) -> None:
    asyncio.run(run_gitlab_review(working_dir, args.mr_link))


def _dispatch_review(args, working_dir: Path) -> None:
    plan_file = _resolve_input_path(args.plan, working_dir)
    asyncio.run(run_review(working_dir, plan_file))


def _dispatch_cr(args, working_dir: Path) -> None:
    asyncio.run(run_prompt_review(working_dir, args.prompt))


def _dispatch_lint_fix(args, working_dir: Path) -> None:
    asyncio.run(run_lint_fix(working_dir, report_only=args.report_only))


_COMMAND_HANDLERS = {
    "review": _dispatch_review,
    "cr": _dispatch_cr,
    "issue": _dispatch_issue,
    "gitlab-review": _dispatch_gitlab_review,
    "lint-fix": _dispatch_lint_fix,
}


def _dispatch_feature(args, working_dir: Path, *, use_worktree: bool) -> bool:
    """Handle `run`/`plan`, the only commands taking a feature name and
    worktree flag. Returns True if it handled the command."""
    if args.command == "run":
        plan_file = _resolve_input_path(args.plan, working_dir)
        approve_plan = _prompt_plan_approval if args.manually_approve_plan else None
        try:
            asyncio.run(
                run_sprint(
                    working_dir,
                    args.feature_name,
                    args.request,
                    use_worktree=use_worktree,
                    plan_file=plan_file,
                    source_branch=args.source_branch,
                    approve_plan=approve_plan,
                    resume_at=args.resume_at,
                )
            )
        except PlanNotApprovedError as exc:
            print(f"\n{exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return True
    if args.command == "plan":
        asyncio.run(
            run_plan(
                working_dir,
                args.feature_name,
                args.request,
                use_worktree=use_worktree,
                source_branch=args.source_branch,
            )
        )
        return True
    return False


def _dispatch(args, working_dir: Path, *, use_worktree: bool) -> None:
    if _dispatch_feature(args, working_dir, use_worktree=use_worktree):
        return
    _COMMAND_HANDLERS[args.command](args, working_dir)


def _requires_clean_tree(args) -> bool:
    """`run`/`issue` always edit in place; `lint-fix` only does unless
    --report-only, which fixes nothing and is as read-only as `cr`.

    `run` skips the check only when BOTH hold: a worktree is being created
    for this invocation (worktree mode, i.e. not --no-worktree) AND
    --source-branch was explicitly given for it -- the worktree is then
    built from that branch, not the main checkout's current state, so the
    main checkout's own uncommitted changes are irrelevant to it. Every
    other combination -- no worktree, or a worktree with no source branch
    -- keeps the check exactly as before.
    """
    if args.command == "run":
        return not (not args.no_worktree and args.source_branch)
    if args.command == "issue":
        return True
    return args.command == "lint-fix" and not args.report_only


def cli_main():
    configure_logging()
    parser = _build_arg_parser()
    args = parser.parse_args()
    working_dir = Path(args.working_dir).resolve()
    log_working_directory(working_dir)
    use_worktree = _should_use_worktree(args)

    _validate_feature_name_requirement(parser, args)
    if _requires_clean_tree(args):
        try:
            _ensure_clean_tree(working_dir)
        except DirtyWorkingTreeError as exc:
            logger.warning("run_blocked_uncommitted_changes")
            print(f"\nWARNING: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        logger.info("run_initialized", command=args.command)
    _boot_repo(
        working_dir, include_gitignore=use_worktree or args.command == "issue"
    )
    _dispatch(args, working_dir, use_worktree=use_worktree)


if __name__ == "__main__":
    cli_main()
