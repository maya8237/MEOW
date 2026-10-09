"""
meow/native_cli.py

The `meow native ...` command group: argparse wiring and JSON output for the
deterministic helpers in `native.native`. Every subcommand prints exactly one
JSON document on stdout and exits 0; any failure prints its message on
stderr and exits 1 (logs also go to stderr, so stdout stays parseable).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from meow.hooks.handlers import HANDLERS
from meow.native import native
from meow.project.config import load_config


def _add_dirs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--working-dir",
        "--work-dir",
        "-d",
        dest="working_dir",
        default=".",
        help="Project root holding .meow/config.toml (default: current directory).",
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
        "--no-worktree",
        "-n",
        dest="no_worktree",
        action="store_true",
        help="Work in the project directory instead of a .worktrees entry.",
    )
    parser.add_argument(
        "--source-branch",
        "--from",
        "-b",
        dest="source_branch",
        default=None,
        help="Branch a fresh worktree is created from.",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Create/reuse a worktree on this named branch (issue flow).",
    )
    parser.add_argument(
        "--existing-branch",
        dest="existing_branch",
        default=None,
        help=(
            "Check out this EXISTING branch (local or origin's) into a "
            "worktree, or (with --no-worktree) require it already checked "
            "out in place -- branch-review flow. Errors if it doesn't "
            "exist anywhere, unlike --branch."
        ),
    )
    parser.add_argument(
        "--allow-dirty",
        dest="allow_dirty",
        action="store_true",
        help="Skip the uncommitted-changes check (read-only flows like plan).",
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
        "--no-lint",
        action="store_true",
        help="Validate configuration only; skip the configured lint checks.",
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
        "--file",
        dest="file",
        default=None,
        help="Lint (and auto-fix) just this file, like the SDK post-edit hook.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Project-wide: apply each command's fix flag before checking.",
    )
    parser.add_argument(
        "--all-blocking",
        dest="all_blocking",
        action="store_true",
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


def _add_checkpoint(sub) -> None:
    parser = sub.add_parser("checkpoint", help="Write a durable native run phase.")
    _add_dirs(parser)
    parser.add_argument("phase")
    parser.add_argument("--run-id")
    parser.add_argument("--request", default="")
    parser.add_argument("--plan")
    parser.add_argument("--review")
    parser.add_argument("--round", type=int)
    parser.add_argument("--reviewer", choices=("PASS", "FAIL"))
    parser.add_argument("--tester", choices=("PASS", "FAIL"))
    finish = sub.add_parser(
        "finalize", help="Run current gates and finish a native run."
    )
    _add_dirs(finish)
    finish.add_argument("run_id")


def _add_prompt(sub) -> None:
    parser = sub.add_parser("prompt", help="Print a role's system prompt as JSON.")
    _add_dirs(parser)
    parser.add_argument("role", choices=native.PROMPT_ROLES)
    parser.add_argument("--plan", default=None, help="Sprint plan file.")
    parser.add_argument(
        "--focus",
        default=None,
        help="Review focus (reviewer-plan) or free-text prompt (reviewer-prompt).",
    )
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="The run used an isolated worktree (enables hygiene review).",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Target branch (reviewer-branch role).",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Branch under review (reviewer-branch role).",
    )
    parser.add_argument(
        "--provider",
        dest="remote_provider",
        choices=("gitlab", "github"),
        default="gitlab",
        help="Remote provider for reviewer-mr (default: gitlab).",
    )
    parser.add_argument("--shape", default=None, help="Accepted shape artifact path.")


def _add_push(sub) -> None:
    parser = sub.add_parser("push", help="Push a named branch to origin.")
    _add_dirs(parser)
    parser.add_argument("branch", help="Branch to push.")


def _add_knowledge(sub) -> None:
    parser = sub.add_parser(
        "knowledge-audit", help="Report-only project knowledge audit."
    )
    _add_dirs(parser)
    parser = sub.add_parser(
        "knowledge-check", help="Run deterministic structural knowledge checks."
    )
    _add_dirs(parser)
    parser = sub.add_parser(
        "knowledge-create", help="Create explicitly selected knowledge documents."
    )
    _add_dirs(parser)
    parser.add_argument("--finding", action="append", required=True)
    parser.add_argument("--overwrite", action="store_true")


def _add_hook(sub) -> None:
    parser = sub.add_parser("hook", help="Run a Claude Code JSON hook handler.")
    _add_dirs(parser)
    parser.add_argument("name", choices=tuple(HANDLERS))
    parser = sub.add_parser(
        "shape-assess", help="Assess whether optional shaping is useful."
    )
    parser.add_argument("request")
    parser = sub.add_parser("shape-create", help="Persist a shape artifact from JSON.")
    _add_dirs(parser)
    parser.add_argument("path")
    parser.add_argument("--json", required=True)
    parser = sub.add_parser("shape-reflect", help="Reflect on a breadboard artifact.")
    _add_dirs(parser)
    parser.add_argument("path")


def add_native_parser(
    subparsers: argparse._SubParsersAction,
    *,
    help: str | None = None,
) -> None:
    native_parser = subparsers.add_parser(
        "native",
        help=help
        if help is not None
        else (
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
    _add_checkpoint(sub)
    _add_prompt(sub)
    _add_push(sub)
    _add_knowledge(sub)
    _add_hook(sub)


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


def _checkpoint(args, working_dir: Path, active: Path) -> dict:
    return native.checkpoint(
        working_dir,
        active,
        args.phase,
        run_id=args.run_id,
        request=args.request,
        plan_file=_resolve(args.plan, active),
        review_file=_resolve(args.review, active),
        round_num=args.round,
        reviewer=args.reviewer,
        tester=args.tester,
    )


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
        remote_provider=args.remote_provider,
        shape_path=_resolve(args.shape, active),
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
    "checkpoint": _checkpoint,
    "finalize": lambda args, wd, active: asyncio.run(
        native.finalize(wd, active, args.run_id)
    ),
    "prompt": _prompt,
    "push": lambda args, wd, active: native.push(active, args.branch),
    "knowledge-audit": lambda args, wd, active: native.knowledge_audit(active),
    "knowledge-check": lambda args, wd, active: native.knowledge_check(active),
    "knowledge-create": lambda args, wd, active: native.knowledge_create(
        active, args.finding, overwrite=args.overwrite
    ),
    "shape-assess": lambda args, wd, active: native.shape_assess(args.request),
    "shape-create": lambda args, wd, active: native.shape_create(
        _resolve(args.path, active), json.loads(args.json)
    ),
    "shape-reflect": lambda args, wd, active: native.shape_reflect(
        _resolve(args.path, active)
    ),
    "hook": lambda args, wd, active: HANDLERS[args.name](json.load(sys.stdin)),
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
