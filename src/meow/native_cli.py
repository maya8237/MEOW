"""
meow/native_cli.py

The `meow native ...` command group: argparse wiring and JSON output for the
deterministic helpers in `native.py`. Every subcommand prints exactly one
JSON document on stdout and exits 0; any failure prints its message on
stderr and exits 1 (logs also go to stderr, so stdout stays parseable).
"""

import argparse
import json
import sys
from pathlib import Path

from meow import native
from meow.config import load_config


def _add_dirs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--working-dir", "--work-dir", "-d",
        dest="working_dir",
        default=".",
        help="Project root holding .harness.toml (default: current directory).",
    )
    parser.add_argument(
        "--active-dir",
        dest="active_dir",
        default=None,
        help=(
            "Directory to operate in -- the worktree `prepare` returned. "
            "Defaults to --working-dir."
        ),
    )


def _add_prepare(sub) -> None:
    parser = sub.add_parser("prepare", help="Run startup guards; resolve worktree.")
    _add_dirs(parser)
    parser.add_argument("--name", default=None, help="Feature name.")
    parser.add_argument(
        "--no-worktree", "-n", dest="no_worktree", action="store_true",
        help="Work in the project directory instead of a .worktrees entry.",
    )


def _add_verify(sub) -> None:
    parser = sub.add_parser(
        "verify",
        help=(
            "Validate and report the project's MEOW configuration "
            "without changing files."
        ),
    )
    _add_dirs(parser)
    parser.add_argument(
        "--no-lint", action="store_true",
        help="Validate configuration only; skip the configured lint checks.",
    )
    parser.add_argument(
        "--source-branch", "--from", "-b", dest="source_branch", default=None,
        help="Branch a fresh worktree is created from.",
    )
    parser.add_argument(
        "--branch", default=None,
        help="Create/reuse a worktree on this named branch (issue flow).",
    )
    parser.add_argument(
        "--existing-branch", dest="existing_branch", default=None,
        help=(
            "Check out this EXISTING branch (local or origin's) into a "
            "worktree, or (with --no-worktree) require it already checked "
            "out in place -- branch-review flow. Errors if it doesn't "
            "exist anywhere, unlike --branch."
        ),
    )
    parser.add_argument(
        "--allow-dirty", dest="allow_dirty", action="store_true",
        help="Skip the uncommitted-changes check (read-only flows like plan).",
    )


def _add_lookups(sub) -> None:
    for name, text in (
        ("latest-plan", "Most recent sprint plan in docs_dir."),
        ("latest-review", "Most recent review file in docs_dir."),
    ):
        _add_dirs(sub.add_parser(name, help=text))


def _add_verdict(sub) -> None:
    parser = sub.add_parser("verdict", help="Read PASS/FAIL from a review file.")
    _add_dirs(parser)
    parser.add_argument("review_file", help="Path to the review file.")


def _add_lint(sub) -> None:
    parser = sub.add_parser("lint", help="Run the configured lint plan.")
    _add_dirs(parser)
    parser.add_argument(
        "--file", dest="file", default=None,
        help="Lint (and auto-fix) just this file, like the SDK post-edit hook.",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Project-wide: apply each command's fix flag before checking.",
    )
    parser.add_argument(
        "--all-blocking", dest="all_blocking", action="store_true",
        help=(
            "Treat every configured command as blocking regardless of "
            "`gate` (lint-fix must fix everything CLI mode would, not just "
            "the review's gate commands)."
        ),
    )


def _add_round(sub) -> None:
    parser = sub.add_parser("round", help="Advance/read the on-disk round counter.")
    _add_dirs(parser)
    parser.add_argument("plan", help="Path to the sprint plan file.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reset", action="store_true", help="Set the counter to 0.")
    mode.add_argument("--show", action="store_true", help="Read without advancing.")


def _add_prompt(sub) -> None:
    parser = sub.add_parser("prompt", help="Print a role's system prompt as JSON.")
    _add_dirs(parser)
    parser.add_argument("role", choices=native.PROMPT_ROLES)
    parser.add_argument("--plan", default=None, help="Sprint plan file.")
    parser.add_argument(
        "--focus", default=None,
        help="Review focus (reviewer-plan) or free-text prompt (reviewer-prompt).",
    )
    parser.add_argument(
        "--worktree", action="store_true",
        help="The run used an isolated worktree (enables hygiene review).",
    )
    parser.add_argument(
        "--target", default=None, help="Target branch (reviewer-branch role).",
    )
    parser.add_argument(
        "--branch", default=None, help="Branch under review (reviewer-branch role).",
    )


def _add_push(sub) -> None:
    parser = sub.add_parser("push", help="Push a named branch to origin.")
    _add_dirs(parser)
    parser.add_argument("branch", help="Branch to push.")


def add_native_parser(subparsers: argparse._SubParsersAction) -> None:
    native_parser = subparsers.add_parser(
        "native",
        help=(
            "Agent-free helpers for skill-driven (in-Claude-Code-session) "
            "runs. Prints JSON."
        ),
    )
    sub = native_parser.add_subparsers(dest="native_command", required=True)
    _add_prepare(sub)
    _add_verify(sub)
    _add_lookups(sub)
    _add_verdict(sub)
    _add_lint(sub)
    _add_round(sub)
    _add_prompt(sub)
    _add_push(sub)


def _resolve(path: str | None, base: Path) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _prepare(args, working_dir: Path, _active: Path) -> dict:
    options = native.PrepareOptions(
        name=args.name,
        use_worktree=not args.no_worktree,
        source_branch=args.source_branch,
        branch=args.branch,
        existing_branch=args.existing_branch,
        require_clean=not args.allow_dirty,
    )
    return native.prepare(working_dir, options)


def _verify(args, working_dir: Path, active: Path) -> dict:
    return native.verify(working_dir, active, run_lint=not args.no_lint)


def _lint(args, working_dir: Path, active: Path) -> dict:
    options = native.LintOptions(
        file_path=args.file, fix=args.fix, all_blocking=args.all_blocking
    )
    return native.lint(working_dir, active, options)


def _round(args, working_dir: Path, active: Path) -> dict:
    mode = "reset" if args.reset else "show" if args.show else "next"
    max_rounds = load_config(working_dir)["max_rounds"]
    return native.round_state(_resolve(args.plan, active), max_rounds, mode)


def _prompt(args, working_dir: Path, active: Path) -> dict:
    return native.role_prompt(
        working_dir,
        active,
        args.role,
        plan_file=_resolve(args.plan, active),
        focus=args.focus,
        use_worktree=args.worktree,
        target=args.target,
        branch=args.branch,
    )


_HANDLERS = {
    "prepare": _prepare,
    "verify": _verify,
    "latest-plan": lambda args, wd, active: native.latest_plan(wd, active),
    "latest-review": lambda args, wd, active: native.latest_review(wd, active),
    "verdict": lambda args, wd, active: native.verdict(
        _resolve(args.review_file, active)
    ),
    "lint": _lint,
    "round": _round,
    "prompt": _prompt,
    "push": lambda args, wd, active: native.push(active, args.branch),
}


def run_native(args) -> None:
    """Dispatch one `meow native` subcommand, printing its JSON result."""
    working_dir = Path(args.working_dir).resolve()
    active_dir = _resolve(args.active_dir, working_dir) or working_dir
    try:
        result = _HANDLERS[args.native_command](args, working_dir, active_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"meow native {args.native_command}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result))
