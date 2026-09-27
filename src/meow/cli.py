"""CLI entry point for the meow harness."""

import argparse
import asyncio
import re
from pathlib import Path

from meow.orchestrator import (
    _boot_repo,
    run_plan,
    run_prompt_review,
    run_review,
    run_sprint,
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--project-root", default=".",
        help="Path to the project repo (default: current directory).",
    )
    parser.add_argument(
        "--worktree",
        default=None,
        help=(
            "Use a dedicated worktree named this value under .worktrees; "
            "default is the slugified feature name."
        ),
    )
    parser.add_argument(
        "--no-worktree",
        action="store_true",
        help="Run in the main repo instead of creating/using a .worktrees entry.",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Plan, implement, and review a feature request end to end."
    )
    run_parser.add_argument("request", help="Feature request text.")
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
        "--plan-file",
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
    args = _build_arg_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    use_worktree = not args.no_worktree
    _boot_repo(project_root, include_gitignore=use_worktree)

    if args.command == "run":
        feature_name = args.worktree or _slugify(args.request)
        asyncio.run(
            run_sprint(
                project_root,
                feature_name,
                args.request,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )
    elif args.command == "plan":
        feature_name = args.worktree or _slugify(args.request)
        asyncio.run(
            run_plan(
                project_root,
                feature_name,
                args.request,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )
    elif args.command == "review":
        plan_file = Path(args.plan_file).resolve() if args.plan_file else None
        asyncio.run(
            run_review(
                project_root,
                plan_file,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )
    elif args.command == "cr":
        prompt_label = args.prompt or "git-diff-review"
        feature_name = args.worktree or _slugify(prompt_label)
        asyncio.run(
            run_prompt_review(
                project_root,
                feature_name,
                args.prompt,
                use_worktree=use_worktree,
                worktree_name=args.worktree,
            )
        )


if __name__ == "__main__":
    cli_main()
