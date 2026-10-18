"""argparse wiring for the meow command-line interface."""

import argparse
import sys
from importlib.metadata import version

from meow.cli.catalog_cli import add_catalog_parsers
from meow.cli.schedule_cli import add_schedule_parser
from meow.native.native_cli import add_native_parser
from meow.ui import DEFAULT_PORT

_VISIBLE_COMMANDS = (
    "run",
    "review",
    "plan",
    "orchestrate",
    "status",
    "cancel",
    "worktree",
    "resume",
    "hooks",
    "ipython",
    "workflows",
    "agents",
    "ui",
    "schedule",
)


# Shortcut -> argv replacing it. `l` has no subcommand of its own; it is
# `run --lint-fix`.
_SHORTCUTS = {
    "r": ("run",),
    "p": ("plan",),
    "o": ("orchestrate",),
    "rv": ("review",),
    "l": ("run", "--lint-fix"),
    "s": ("status",),
    "x": ("cancel",),
    "c": ("resume",),
    "q": ("queue",),
    "w": ("worktree",),
    "h": ("hooks",),
    "i": ("ipython",),
}

# Easter egg: always parseable, only listed by `--help --fun`.
_FUN_SHORTCUTS = {
    "pounce": ("run",),
    "hunt": ("run",),
    "sniff": ("plan",),
    "stalk": ("plan",),
    "purr": ("review",),
    "lick": ("review",),
    "groom": ("run", "--lint-fix"),
    "scratch": ("run", "--lint-fix"),
    "peek": ("status",),
    "hiss": ("cancel",),
    "wake": ("resume",),
    "herd": ("queue",),
    "nest": ("worktree",),
    "claws": ("hooks",),
    "lab": ("ipython",),
}


def _format_shortcuts(shortcuts: dict[str, tuple[str, ...]]) -> str:
    return "  ".join(f"{k}={' '.join(v)}" for k, v in shortcuts.items())


def expand_shortcuts(argv: list[str]) -> list[str]:
    """Rewrite a leading shortcut command into its canonical argv."""
    if argv and not argv[0].startswith("-"):
        target = _SHORTCUTS.get(argv[0]) or _FUN_SHORTCUTS.get(argv[0])
        if target:
            return [*target, *argv[1:]]
    return argv


class _MeowParser(argparse.ArgumentParser):
    """Root parser: expands command shortcuts and the hidden `--help --fun`."""

    def parse_known_args(self, args=None, namespace=None):
        argv = list(sys.argv[1:] if args is None else args)
        if "--fun" in argv and ("--help" in argv or "-h" in argv):
            self.print_help()
            print(f"\ncat shortcuts:\n  {_format_shortcuts(_FUN_SHORTCUTS)}")
            self.exit()
        parsed, rest = super().parse_known_args(expand_shortcuts(argv), namespace)
        if (parsed.update or parsed.check_update) and parsed.command:
            self.error("update options cannot be combined with a command")
        if not (parsed.update or parsed.check_update or parsed.command):
            self.error("a command or --update is required")
        return parsed, rest


def _add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--work-dir",
        "-d",
        dest="working_dir",
        default=".",
        help="Working directory for the harness (default: current directory).",
    )


def _add_feature_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--name",
        "-f",
        dest="feature_name",
        default=None,
        help=(
            "Feature name used for generated files and as the worktree name "
            "when worktrees are enabled. Required for plain build mode "
            "unless --no-worktree/-n is supplied; meaningless (and "
            "rejected) with --jira/--lint-fix, which derive their own "
            "worktree names."
        ),
    )
    parser.add_argument(
        "--no-worktree",
        "-n",
        dest="no_worktree",
        action="store_true",
        help="Run in the main repo instead of creating/using a .worktrees entry.",
    )
    parser.add_argument(
        "--from",
        "-b",
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
            "state, so its own uncommitted changes don't apply to it. "
            "Plain build mode only."
        ),
    )


def _add_manual_approval_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--manually-approve-plan",
        "-m",
        dest="manually_approve_plan",
        action="store_true",
        help=(
            "Show the plan after the planner writes it and ask for "
            "approval before the generator implements it. Declining exits "
            "without running the generator. Plain build mode and --jira "
            "only."
        ),
    )


def _add_hidden_parser(
    subparsers: argparse._SubParsersAction, name: str
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=argparse.SUPPRESS)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if action.dest != name
    ]
    return parser


def _build_arg_parser() -> argparse.ArgumentParser:  # ruff: ignore[too-many-statements, too-many-locals]
    parser = _MeowParser(
        prog="meow",
        epilog=f"shortcuts:\n  {_format_shortcuts(_SHORTCUTS)}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"meow {version('meow')}",
    )
    updates = parser.add_mutually_exclusive_group()
    updates.add_argument(
        "--update",
        "-u",
        dest="update",
        action="store_true",
        help="Install the latest published MEOW release.",
    )
    updates.add_argument(
        "--check-update",
        action="store_true",
        help="Only check for a newer version and print an update warning.",
    )
    subparsers = parser.add_subparsers(
        parser_class=argparse.ArgumentParser,
        dest="command",
        required=False,
        metavar="{" + ",".join(_VISIBLE_COMMANDS) + "}",
    )

    _add_run_parser(subparsers)
    add_schedule_parser(subparsers)
    scheduled = _add_hidden_parser(subparsers, "_scheduled")
    scheduled.add_argument("schedule_id")
    _add_common_args(scheduled)
    add_catalog_parsers(subparsers)
    ui_parser = subparsers.add_parser(
        "ui", help="Browse workflows, agents, and skills in a local page."
    )
    ui_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    ui_parser.add_argument("--no-browser", action="store_true")
    _add_common_args(ui_parser)
    worker_parser = _add_hidden_parser(subparsers, "_worker")
    worker_parser.add_argument("run_id")
    worker_parser.add_argument("--nonce", required=True)
    _add_common_args(worker_parser)
    orchestrator_task_parser = _add_hidden_parser(subparsers, "_orchestrator-task")
    orchestrator_task_parser.add_argument("orchestrator_id")
    orchestrator_task_parser.add_argument("task_id")
    orchestrator_task_parser.add_argument(
        "--resume-at",
        dest="resume_at",
        choices=["generate", "review"],
        default="generate",
    )
    orchestrator_task_parser.add_argument("--plan", default=None)
    orchestrator_task_parser.add_argument("--test", action="store_true")
    orchestrator_task_parser.add_argument("--preexisting", action="store_true")
    _add_common_args(orchestrator_task_parser)
    _add_review_parser(subparsers)
    _add_plan_parser(subparsers)
    _add_orchestrate_parser(subparsers)
    status_parser = subparsers.add_parser("status", help="Inspect a saved run.")
    status_parser.add_argument("run_id", nargs="?")
    status_parser.add_argument("--verbose", "-v", action="store_true")
    _add_common_args(status_parser)
    cancel_parser = subparsers.add_parser(
        "cancel", help="Request a running sprint to stop."
    )
    cancel_parser.add_argument("run_id")
    _add_common_args(cancel_parser)
    worktree_parser = subparsers.add_parser(
        "worktree", help="Inspect and safely clean run worktrees."
    )
    worktree_sub = worktree_parser.add_subparsers(
        dest="worktree_command", required=True
    )
    list_parser = worktree_sub.add_parser("list", help="List saved run worktrees.")
    _add_common_args(list_parser)
    for action in ("inspect", "clean"):
        child = worktree_sub.add_parser(
            action, help=f"{action.title()} a run worktree."
        )
        child.add_argument("run_id")
        _add_common_args(child)
    evaluate_parser = _add_hidden_parser(subparsers, "evaluate")
    evaluate_parser.add_argument("run_id", nargs="?")
    evaluate_parser.add_argument("--compare", action="append", default=[])
    evaluate_parser.add_argument("--verbose", "-v", action="store_true")
    _add_common_args(evaluate_parser)
    resume_parser = subparsers.add_parser(
        "resume", help="Inspect or continue a saved run."
    )
    resume_parser.add_argument("run_id", nargs="?")
    resume_parser.add_argument("--continue", dest="continue_run", action="store_true")
    resume_parser.add_argument(
        "--request",
        metavar="TEXT",
        help=(
            "Clarified request for a run paused for a product decision "
            "before planning; requires --continue."
        ),
    )
    resume_parser.add_argument(
        "--repair",
        action="store_true",
        help=(
            "Re-baseline resume gates that block a run: adopt the current plan, "
            "config, branch, and workflow, relocate a moved worktree, and "
            "start fresh sessions for missing Claude session references."
        ),
    )
    _add_common_args(resume_parser)
    detach_parser = _add_hidden_parser(subparsers, "detach")
    detach_parser.add_argument("--log", metavar="FILE", default=None)
    detach_parser.add_argument("detach_argv", nargs=argparse.REMAINDER)
    _add_common_args(detach_parser)
    queue_parser = subparsers.add_parser(
        "queue", help="Queue a task or run queued tasks serially."
    )
    queue_parser.add_argument("request", nargs="?", default=None)
    queue_parser.add_argument("--name", dest="feature_name", default=None)
    _add_common_args(queue_parser)
    docs_update_parser = _add_hidden_parser(subparsers, "docs-update")
    docs_update_parser.add_argument("--since", metavar="REF")
    _add_common_args(docs_update_parser)
    add_native_parser(subparsers, help=argparse.SUPPRESS)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if action.dest != "native"
    ]
    hooks = subparsers.add_parser("hooks", help="Manage optional host hooks.")
    hooks_sub = hooks.add_subparsers(dest="hooks_command", required=True)
    install = hooks_sub.add_parser("install", help="Install selected Claude hooks.")
    install.add_argument("host", choices=("claude",))
    install.add_argument(
        "--only",
        action="append",
        choices=("lint", "shaping", "plan_capture", "plan_stop"),
    )
    install.add_argument("--dry-run", action="store_true")
    _add_common_args(install)
    uninstall = hooks_sub.add_parser(
        "uninstall", help="Remove MEOW-owned Claude hooks."
    )
    uninstall.add_argument("host", choices=("claude",))
    _add_common_args(uninstall)
    hook_status = hooks_sub.add_parser("status", help="Inspect installed Claude hooks.")
    hook_status.add_argument("host", choices=("claude",))
    _add_common_args(hook_status)

    subparsers.add_parser("ipython", help="Open the interactive MEOW IPython session.")

    return parser


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    run_parser = subparsers.add_parser(
        "run",
        help=(
            "Plan, implement, and review a feature request end to end -- "
            "or, with --jira/--lint-fix, build from a Jira issue or fix "
            "lint instead."
        ),
    )
    run_parser.add_argument(
        "--workflow", help="Workflow name (overrides [workflow].default)."
    )
    run_parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Workflow input; repeat for multiple values.",
    )
    run_parser.add_argument(
        "request",
        nargs="?",
        default=None,
        help="Feature request text (required unless --jira is given).",
    )
    run_parser.add_argument(
        "--plan",
        "-p",
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
            "Plain build mode only. Where to resume the round loop when a "
            "plan already exists (via --plan, or auto-detected in "
            "docs_dir when not given): 'generate' (default) starts with "
            "the generator, same as a fresh sprint. 'review' skips "
            "straight to reviewing the existing code first, and only runs "
            "the generator if the review finds something to fix -- for "
            "continuing a sprint that was interrupted after the generator "
            "already produced code, without re-running it on code that's "
            "already there."
        ),
    )
    run_parser.add_argument(
        "--jira",
        nargs="?",
        const="",
        default=None,
        metavar="KEY",
        help=(
            "Fetch this Jira issue (omit KEY for the latest in "
            "[jira].project_key) and build it end to end in a pushed "
            "worktree branch."
        ),
    )
    run_parser.add_argument(
        "-a",
        "--auto",
        dest="unattended",
        action="store_true",
        help=(
            "Deliver a verified isolated-worktree run without interactive "
            "handoff; required for --background."
        ),
    )
    # Durable schedules and workers created before --auto retain their argv.
    run_parser.add_argument(
        "--unattended", dest="unattended", action="store_true", help=argparse.SUPPRESS
    )
    run_parser.add_argument(
        "--background",
        action="store_true",
        help="Continue an unattended run in a detached local worker.",
    )
    run_parser.add_argument(
        "--test",
        action="store_true",
        help="Run configured tests and exploratory testing after a passing review.",
    )
    run_parser.add_argument(
        "--lint-fix",
        dest="lint_fix",
        action="store_true",
        help="Run every configured lint command and fix what it finds.",
    )
    run_parser.add_argument(
        "--report-only",
        dest="report_only",
        action="store_true",
        help=(
            "--lint-fix mode only: only run the configured lint commands "
            "and report failures -- apply no fixes and run no agent. Used "
            "by the lint-fix skill wrapper, which fixes what's reported "
            "itself."
        ),
    )
    _add_common_args(run_parser)
    _add_feature_args(run_parser)
    _add_manual_approval_arg(run_parser)


def _add_review_parser(subparsers: argparse._SubParsersAction) -> None:
    review_parser = subparsers.add_parser(
        "review",
        help=(
            "Review existing code (and, with --fix, fix it) from a "
            "prompt, --ci, --jira, --gitlab, --github, --branch, or a plan file."
        ),
    )
    review_parser.add_argument(
        "--scope",
        choices=("auto", "project", "diff"),
        default="auto",
        help=(
            "Prompt/Jira scope; project inventories every code area "
            "and skips plan discovery."
        ),
    )
    review_parser.add_argument(
        "--ci",
        action="store_true",
        help="Review the exact GitLab pipeline checkout and emit CI artifacts.",
    )
    review_parser.add_argument(
        "--target-ref",
        default=None,
        help=(
            "CI target Git ref (default: refs/remotes/origin/<[delivery]."
            "target_branch>, which defaults to dev)."
        ),
    )
    review_parser.add_argument(
        "--artifact-dir",
        default=None,
        help="CI artifact directory (default: .meow/ci-artifacts).",
    )
    review_parser.add_argument(
        "request",
        nargs="?",
        default=None,
        help=(
            "Free-text review prompt/basis. Optional -- with no other "
            "source either, falls back to the latest plan in docs_dir, "
            "then to the git diff/whole project."
        ),
    )
    review_parser.add_argument(
        "--plan",
        "-p",
        dest="plan",
        default=None,
        help=(
            "Review (and, with --fix, loop-fix) this plan's implementation "
            "instead of auto-discovering the latest one."
        ),
    )
    review_parser.add_argument(
        "--jira",
        nargs="?",
        const="",
        default=None,
        metavar="KEY",
        help=(
            "Fetch this Jira issue (omit KEY for the latest in "
            "[jira].project_key) and review the current code against what "
            "it asked for."
        ),
    )
    review_parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Loop review and fixes to max_rounds (raising if it never "
            "passes) instead of a single report-only pass. Implied when "
            "--review-file is given."
        ),
    )
    review_parser.add_argument(
        "--test",
        action="store_true",
        help="Run tests after an explicit plan review.",
    )
    review_parser.add_argument(
        "--gitlab",
        dest="gitlab",
        default=None,
        metavar="MR-LINK",
        help=(
            "Grade a GitLab merge request's diff. Always read-only -- "
            "there is no local checkout to fix, so this can't be combined "
            "with --fix."
        ),
    )
    review_parser.add_argument(
        "--github",
        dest="github",
        default=None,
        metavar="PR-LINK",
        help=(
            "Grade a GitHub pull request's diff. Always read-only -- "
            "there is no local checkout to fix, so this can't be combined "
            "with --fix."
        ),
    )
    review_parser.add_argument(
        "--branch",
        dest="branch",
        default=None,
        help="Review this local branch's diff against --target.",
    )
    review_parser.add_argument(
        "--target",
        dest="target",
        default=None,
        help="Target branch for --branch (required together with it).",
    )
    review_parser.add_argument(
        "--review-file",
        "-r",
        dest="review_file",
        default=None,
        help=(
            "Resume fixing this existing review file instead of running a "
            "fresh review (auto-detects its flavor; implies --fix)."
        ),
    )
    review_parser.add_argument(
        "--no-worktree",
        "-n",
        dest="no_worktree",
        action="store_true",
        help=(
            "--branch source only: fix in place instead of an isolated "
            "worktree (requires the branch already checked out)."
        ),
    )
    _add_common_args(review_parser)


def _add_plan_parser(subparsers: argparse._SubParsersAction) -> None:
    plan_parser = subparsers.add_parser(
        "plan",
        help="Write a sprint plan for a feature request, without implementing it.",
    )
    plan_parser.add_argument("request", help="Feature request text.")
    _add_common_args(plan_parser)
    _add_feature_args(plan_parser)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("must be an integer >= 1")
    return number


def _add_orchestrate_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "orchestrate",
        help=(
            "Split an objective into tasks, run them in parallel, merge them "
            "into one branch, review it, and open a pull request."
        ),
    )
    parser.add_argument(
        "words",
        nargs="*",
        metavar="WORDS",
        help=(
            "The objective text, or an action: 'status [ID]', 'resume ID', "
            "'cancel ID'. Give exactly one of the objective, --jira, --jql, "
            "or --tasks to start."
        ),
    )
    parser.add_argument(
        "--jira",
        nargs="+",
        default=None,
        metavar="KEY",
        help="Resolve these Jira issues (read-only; needs [jira] and [jira.mcp]).",
    )
    parser.add_argument(
        "--jql",
        default=None,
        metavar="QUERY",
        help="Resolve every Jira issue matching this JQL query.",
    )
    parser.add_argument(
        "--tasks",
        dest="tasks_file",
        default=None,
        metavar="FILE",
        help="Run the tasks listed in this TOML or JSON file; no decomposition.",
    )
    parser.add_argument(
        "--from",
        dest="source_branch",
        default=None,
        metavar="BRANCH",
        help="Base branch for the integration branch (default: the current branch).",
    )
    parser.add_argument(
        "--max-parallel",
        dest="max_parallel",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Tasks to run at once (default: [orchestrator].max_parallel).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Stop after decomposition and print the task graph.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run configured tests and exploratory testing in every task.",
    )
    parser.add_argument(
        "--no-pr",
        dest="no_pr",
        action="store_true",
        help="Push the integration branch but do not open a pull request.",
    )
    parser.add_argument(
        "--task",
        dest="task_id",
        default=None,
        metavar="TASK",
        help="resume only: the blocked task to reset (default: the blocker's task).",
    )
    parser.add_argument(
        "--request",
        default=None,
        metavar="TEXT",
        help="resume only: replacement request text for that task.",
    )
    _add_common_args(parser)
