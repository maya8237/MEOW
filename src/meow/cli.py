"""CLI entry point for the meow harness."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from meow.gitlab_reviewer import run_gitlab_review
from meow.issue_solver import IssueUnresolvedError, run_issue_solver
from meow.logging import configure_logging, get_logger
from meow.orchestrator import (
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
    _add_common_args(run_parser)
    _add_feature_args(run_parser)


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


def _dispatch_issue(args, working_dir: Path) -> None:
    try:
        result = asyncio.run(run_issue_solver(working_dir, args.issue))
    except IssueUnresolvedError as exc:
        print(f"\nWARNING: could not resolve the issue: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result))


def _dispatch_gitlab_review(args, working_dir: Path) -> None:
    asyncio.run(run_gitlab_review(working_dir, args.mr_link))


def _dispatch_feature(args, working_dir: Path, *, use_worktree: bool) -> bool:
    """Handle `run`/`plan`, the only commands taking a feature name and
    worktree flag. Returns True if it handled the command."""
    if args.command == "run":
        plan_file = _resolve_input_path(args.plan, working_dir)
        asyncio.run(
            run_sprint(
                working_dir,
                args.feature_name,
                args.request,
                use_worktree=use_worktree,
                plan_file=plan_file,
            )
        )
        return True
    if args.command == "plan":
        asyncio.run(
            run_plan(
                working_dir,
                args.feature_name,
                args.request,
                use_worktree=use_worktree,
            )
        )
        return True
    return False


def _dispatch(args, working_dir: Path, *, use_worktree: bool) -> None:
    if _dispatch_feature(args, working_dir, use_worktree=use_worktree):
        return
    if args.command == "review":
        plan_file = _resolve_input_path(args.plan, working_dir)
        asyncio.run(run_review(working_dir, plan_file))
    elif args.command == "cr":
        asyncio.run(run_prompt_review(working_dir, args.prompt))
    elif args.command == "issue":
        _dispatch_issue(args, working_dir)
    elif args.command == "gitlab-review":
        _dispatch_gitlab_review(args, working_dir)


def cli_main():
    configure_logging()
    parser = _build_arg_parser()
    args = parser.parse_args()
    working_dir = Path(args.working_dir).resolve()
    log_working_directory(working_dir)
    use_worktree = _should_use_worktree(args)

    _validate_feature_name_requirement(parser, args)
    if args.command in {"run", "issue"}:
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
