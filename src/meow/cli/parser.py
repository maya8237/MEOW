"""argparse wiring for the meow command-line interface."""

import argparse
from importlib.metadata import version

from meow.native.native_cli import add_native_parser

_VISIBLE_COMMANDS = (
    "run",
    "review",
    "plan",
    "status",
    "cancel",
    "worktree",
    "resume",
    "hooks",
    "ipython",
)


def _add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--working-dir",
        "--work-dir",
        "-d",
        dest="working_dir",
        default=".",
        help="Working directory for the harness (default: current directory).",
    )


def _add_feature_args(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--name",
        "--feature-name",
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
        "--noworktree",
        "-n",
        dest="no_worktree",
        action="store_true",
        help="Run in the main repo instead of creating/using a .worktrees entry.",
    )
    parser.add_argument(
        "--source-branch",
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
    parser = argparse.ArgumentParser(prog="meow")
    parser.add_argument(
        "--version",
        action="version",
        version=f"meow {version('meow')}",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{" + ",".join(_VISIBLE_COMMANDS) + "}",
    )

    _add_run_parser(subparsers)
    worker_parser = _add_hidden_parser(subparsers, "_worker")
    worker_parser.add_argument("run_id")
    worker_parser.add_argument("--nonce", required=True)
    _add_common_args(worker_parser)
    _add_review_parser(subparsers)
    _add_plan_parser(subparsers)
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
    resume_parser.add_argument("--auto-resume", action="store_true")
    _add_common_args(resume_parser)
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
        "request",
        nargs="?",
        default=None,
        help="Feature request text (required unless --jira is given).",
    )
    run_parser.add_argument(
        "--plan",
        "--plan-file",
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
            "plan already exists (via --plan-file, or auto-detected in "
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
        "--unattended",
        action="store_true",
        help="Commit and push a verified run even when working in place.",
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
            "prompt, --jira, --gitlab, --github, --branch, or a plan file."
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
        help="CI target Git ref (default: refs/remotes/origin/dev).",
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
        "--plan-file",
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
            "Loop review-fix-review to max_rounds (raising if it never "
            "passes) instead of a single report-only pass. Implied when "
            "--review-file is given."
        ),
    )
    review_parser.add_argument(
        "--test",
        action="store_true",
        help="Run tests after an explicit plan-file review.",
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
        "--noworktree",
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
