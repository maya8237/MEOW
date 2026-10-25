"""The `meow native ...` command group: argparse wiring and JSON output for the
deterministic helpers in `native.native`. Every subcommand prints exactly one
JSON document on stdout and exits 0; any failure prints its message on
stderr and exits 1 (logs also go to stderr, so stdout stays parseable).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from meow.native import native
from meow.project.config import config_root, load_config
from meow.project.custom import SKILL_ROLES


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
            "Defaults to --work-dir."
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
        "--from",
        "-b",
        dest="source_branch",
        default=None,
        help="Branch a fresh worktree is created from.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Never reuse an existing .worktrees/<name>; take <name>-2, -3, ... "
            "instead. For runs that plan from scratch. The JSON `name` is the "
            "name actually used."
        ),
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
    parser.add_argument(
        "--target", dest="target_branch", help="PR/MR target for a new run."
    )
    finish = sub.add_parser(
        "finalize", help="Run current gates and finish a native run."
    )
    _add_dirs(finish)
    finish.add_argument("run_id")


def _add_model_host(parser) -> None:
    parser.add_argument(
        "--host", default="claude", help="Native host, e.g. claude or codex."
    )
    parser.add_argument(
        "--available-model",
        action="append",
        default=None,
        help="Exact supported model ID; repeat for each available model.",
    )


def _add_prompt(sub) -> None:
    parser = sub.add_parser("prompt", help="Print a role's system prompt as JSON.")
    _add_dirs(parser)
    parser.add_argument("role", choices=native.PROMPT_ROLES)
    parser.add_argument("--scope", choices=("auto", "project", "diff"), default="auto")
    _add_model_host(parser)
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


def _add_push(sub) -> None:
    parser = sub.add_parser("push", help="Push a named branch to origin.")
    _add_dirs(parser)
    parser.add_argument("branch", help="Branch to push.")


def _add_onboard(sub) -> None:
    parser = sub.add_parser(
        "onboard-status",
        help="Report onboarding state and unconfigured add-on features.",
    )
    _add_dirs(parser)


def _add_custom(sub) -> None:
    parser = sub.add_parser(
        "custom", help="List the custom skills and agents in effect."
    )
    _add_dirs(parser)
    _add_model_host(parser)
    parser.add_argument(
        "--role",
        choices=sorted(SKILL_ROLES),
        help="Only what this role receives.",
    )


def _orchestrate_leaf(
    commands, name: str, text: str, id_nargs: str | None = None
) -> argparse.ArgumentParser:
    parser = commands.add_parser(name, help=text)
    _add_dirs(parser)
    parser.add_argument(
        "orch_id", nargs=id_nargs, metavar="ID", help="Orchestrator ID."
    )
    return parser


def _add_orchestrate_init(commands) -> None:
    parser = commands.add_parser(
        "init", help="Create an orchestrator and its worktree; take the driver lock."
    )
    _add_dirs(parser)
    parser.add_argument("--objective", default=None, help="Free-text objective.")
    parser.add_argument("--tasks", dest="tasks_file", default=None, help="Tasks file.")
    parser.add_argument("--from", dest="base", default=None, help="Base branch.")
    parser.add_argument("--target", default=None, help="PR/MR target branch.")
    parser.add_argument("--max-parallel", dest="max_parallel", type=int, default=None)
    parser.add_argument("--test", action="store_true", help="Run tests in each task.")
    parser.add_argument("--no-mr", "--no-pr", dest="no_pr", action="store_true")


def _add_orchestrate_simple(commands) -> None:
    for name, text in (
        ("tick", "Poll children, merge, launch; report what needs the session."),
        ("review", "Run checks and print the objective-review prompt for a round."),
        ("deliver", "Push the integration branch; print the PR title and body."),
        ("cancel", "Cancel the orchestrator and its live children."),
        ("release", "Drop the native driver lock."),
    ):
        parser = _orchestrate_leaf(commands, name, text)
        if name == "review":
            _add_model_host(parser)
    _orchestrate_leaf(commands, "status", "Roll-up of an orchestrator.", "?")
    _orchestrate_leaf(
        commands, "wait", "Block until a child journal changes."
    ).add_argument(
        "--timeout", type=float, default=300.0, help="Seconds to wait (default: 300)."
    )


def _add_orchestrate_options(commands) -> None:
    for name, text in (
        ("tasks", "Validate and save the decomposer output."),
        ("review-result", "Record an objective-review verdict file."),
    ):
        _orchestrate_leaf(commands, name, text).add_argument("--file", required=True)
    for name, text in (
        ("merge-done", "Finish a merge after conflict resolution."),
        ("merge-abort", "Roll a conflicted merge back."),
    ):
        _orchestrate_leaf(commands, name, text).add_argument(
            "--task", dest="task_id", required=True
        )
    triage = _orchestrate_leaf(commands, "triage", "Apply a triage decision file.")
    triage.add_argument("--task", dest="task_id", required=True)
    triage.add_argument("--file", required=True)
    outcome = _orchestrate_leaf(commands, "pr", "Record the PR outcome.")
    group = outcome.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", default=None)
    group.add_argument("--error", default=None)
    resume = _orchestrate_leaf(commands, "resume", "Prepare a stopped orchestrator.")
    resume.add_argument("--task", dest="task_id", default=None)
    resume.add_argument("--request", default=None)


def _add_orchestrate(sub) -> None:
    group = sub.add_parser(
        "run", help="Helpers for /meow:run --orchestrate (JSON out)."
    )
    commands = group.add_subparsers(dest="run_command", required=True)
    _add_orchestrate_init(commands)
    _add_orchestrate_simple(commands)
    _add_orchestrate_options(commands)


def add_native_parser(  # ruff: ignore[too-many-statements] -- register independent helper parsers
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
    checks = sub.add_parser(
        "review-checks", help="Run or inspect durable local review verification."
    )
    _add_dirs(checks)
    check_mode = checks.add_mutually_exclusive_group()
    check_mode.add_argument("--background", action="store_true")
    check_mode.add_argument("--status", action="store_true")
    _add_push(sub)
    _add_onboard(sub)
    _add_custom(sub)
    workflow = sub.add_parser("workflow", help="Print the resolved workflow as JSON.")
    workflow.add_argument("name")
    _add_dirs(workflow)
    _add_orchestrate(sub)


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
        fresh=args.fresh,
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
    max_rounds = load_config(config_root(working_dir, active))["max_rounds"]
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
        target_branch=args.target_branch,
    )


def _model_host_options(args) -> dict:
    options = {"host": args.host}
    if args.available_model is not None:
        options["available_models"] = args.available_model
    return options


def _prompt(args, working_dir: Path, active: Path) -> dict:
    options = _model_host_options(args)
    if args.scope != "auto":
        options["scope"] = args.scope
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
        **options,
    )


def _review_checks(args, working_dir: Path, active: Path) -> dict:
    from meow.native.native_review import review_checks

    return review_checks(
        working_dir, active, background=args.background, status=args.status
    )


_ORCHESTRATE_HANDLERS = {
    "init": lambda mod, args, wd, active: mod.init(
        wd,
        objective=args.objective,
        tasks_file=_resolve(args.tasks_file, active),
        base=args.base,
        target=args.target,
        max_parallel=args.max_parallel,
        test=args.test,
        no_pr=args.no_pr,
    ),
    "tasks": lambda mod, args, wd, active: mod.save_tasks(
        wd, args.orch_id, _resolve(args.file, active)
    ),
    "tick": lambda mod, args, wd, active: mod.tick(wd, args.orch_id),
    "wait": lambda mod, args, wd, active: mod.wait(wd, args.orch_id, args.timeout),
    "merge-done": lambda mod, args, wd, active: mod.merge_done(
        wd, args.orch_id, args.task_id
    ),
    "merge-abort": lambda mod, args, wd, active: mod.merge_abort(
        wd, args.orch_id, args.task_id
    ),
    "triage": lambda mod, args, wd, active: mod.triage(
        wd, args.orch_id, args.task_id, _resolve(args.file, active)
    ),
    "review": lambda mod, args, wd, active: mod.review(
        wd, args.orch_id, **_model_host_options(args)
    ),
    "review-result": lambda mod, args, wd, active: mod.review_result(
        wd, args.orch_id, _resolve(args.file, active)
    ),
    "deliver": lambda mod, args, wd, active: mod.deliver(wd, args.orch_id),
    "pr": lambda mod, args, wd, active: mod.record_pr(
        wd, args.orch_id, url=args.url, error=args.error
    ),
    "status": lambda mod, args, wd, active: mod.status(wd, args.orch_id),
    "cancel": lambda mod, args, wd, active: mod.cancel(wd, args.orch_id),
    "release": lambda mod, args, wd, active: mod.release(wd, args.orch_id),
    "resume": lambda mod, args, wd, active: mod.resume(
        wd, args.orch_id, task_id=args.task_id, request=args.request
    ),
}


def _orchestrate(args, working_dir: Path, active: Path) -> dict:
    # Imported here so every other `meow native` command starts without the
    # orchestrator's heavier imports.
    from meow.native import native_orchestrate

    handler = _ORCHESTRATE_HANDLERS[args.run_command]
    return handler(native_orchestrate, args, working_dir, active)


_HANDLERS = {
    "workflow": lambda args, wd, active: native.workflow(wd, active, args.name),
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
    "review-checks": _review_checks,
    "push": lambda args, wd, active: native.push(active, args.branch),
    "custom": lambda args, wd, active: native.custom(
        wd, active, args.role, **_model_host_options(args)
    ),
    "onboard-status": lambda args, wd, active: native.onboard_status(active),
    "run": _orchestrate,
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
