"""CLI entry point for the meow harness."""

import argparse
import asyncio
from pathlib import Path

from meow.orchestrator import (
    _boot_repo,
    log_working_directory,
    run_plan,
    run_prompt_review,
    run_review,
    run_sprint,
)

DEFAULT_FEATURE_NAME = "feature"


def _add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--working-dir", "--work-dir", "-d",
        dest="working_dir",
        default=".",
        help="Working directory for the harness (default: current directory).",
    )
    parser.add_argument(
        "--worktree", "-w", dest="worktree", default=None,
        help=(
            "Required worktree name under .worktrees unless --no-worktree/-n "
            "is supplied; the skills may synthesize this value themselves."
        ),
    )
    parser.add_argument(
        "--no-worktree", "--noworktree", "-n",
        dest="no_worktree",
        action="store_true",
        help="Run in the main repo instead of creating/using a .worktrees entry.",
    )


def _validate_worktree_requirement(parser: argparse.ArgumentParser, args) -> None:
    requires_name = args.command in {"run", "plan", "cr"} and not args.no_worktree
    if requires_name and args.worktree is None:
        parser.error(
            "--worktree/-w is required unless --no-worktree/-n is supplied"
        )


def _should_use_worktree(args) -> bool:
    return not args.no_worktree and not (
        args.command == "review" and args.worktree is None
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meow")
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    plan_parser = subparsers.add_parser(
        "plan",
        help="Write a sprint plan for a feature request, without implementing it.",
    )
    plan_parser.add_argument("request", help="Feature request text.")
    _add_common_args(plan_parser)

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

    return parser


def cli_main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    working_dir = Path(args.working_dir).resolve()
    log_working_directory(working_dir)
    use_worktree = _should_use_worktree(args)

    _validate_worktree_requirement(parser, args)
    _boot_repo(working_dir, include_gitignore=use_worktree)

    if args.command == "run":
        feature_name = args.worktree or DEFAULT_FEATURE_NAME
        plan_file = Path(args.plan).resolve() if args.plan else None
        asyncio.run(
            run_sprint(
                working_dir,
                feature_name,
                args.request,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
                plan_file=plan_file,
            )
        )
    elif args.command == "plan":
        feature_name = args.worktree or DEFAULT_FEATURE_NAME
        asyncio.run(
            run_plan(
                working_dir,
                feature_name,
                args.request,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )
    elif args.command == "review":
        plan_file = Path(args.plan).resolve() if args.plan else None
        asyncio.run(
            run_review(
                working_dir,
                plan_file,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )
    elif args.command == "cr":
        asyncio.run(
            run_prompt_review(
                working_dir,
                args.worktree,
                args.prompt,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )


if __name__ == "__main__":
    cli_main()
