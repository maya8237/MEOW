"""CLI entry point for the meow harness."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from meow.issue_solver import IssueUnresolvedError, run_issue_solver
from meow.lint_fix import run_lint_fix
from meow.logging import configure_logging, get_logger
from meow.native_cli import add_native_parser, run_native
from meow.orchestrator import PlanNotApprovedError, log_working_directory
from meow.review_cli import run_review_command
from meow.sprint_runner import run_plan, run_sprint
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
            "when worktrees are enabled. Required for plain build mode "
            "unless --no-worktree/-n is supplied; meaningless (and "
            "rejected) with --jira/--lint-fix, which derive their own "
            "worktree names."
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
            "state, so its own uncommitted changes don't apply to it. "
            "Plain build mode only."
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
            "without running the generator. Plain build mode and --jira "
            "only."
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


def _is_plain_build(args) -> bool:
    """True for `run` with neither --lint-fix nor --jira -- the only mode
    that still plans+implements a brand new feature the original way."""
    return args.command == "run" and not args.lint_fix and args.jira is None


def _validate_feature_name_requirement(parser: argparse.ArgumentParser, args) -> None:
    requires_name = (
        args.command == "plan" or _is_plain_build(args)
    ) and not args.no_worktree
    if requires_name and args.feature_name is None:
        parser.error(
            "--name/--feature-name/-f is required unless "
            "--no-worktree/-n is supplied"
        )


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


def _validate_lint_fix_flags(parser: argparse.ArgumentParser, args) -> None:
    if args.request or args.jira is not None:
        parser.error("--lint-fix takes no request text and no --jira")
    checks = (
        *_MISUSE_CHECKS,
        ("-m/--manually-approve-plan", lambda a: a.manually_approve_plan),
    )
    misused = _misused_flags(args, checks)
    if misused:
        joined = ", ".join(misused)
        parser.error(f"--lint-fix doesn't use a worktree or a plan; drop {joined}")


def _validate_build_flags(parser: argparse.ArgumentParser, args) -> None:
    if args.request and args.jira is not None:
        parser.error("Give either a request or --jira, not both")
    if not args.request and args.jira is None:
        parser.error("request is required unless --jira is given")
    if args.jira is not None:
        misused = _misused_flags(args, _MISUSE_CHECKS)
        if misused:
            parser.error(
                "--jira uses its own pushable-branch worktree; drop "
                + ", ".join(misused)
            )


def _validate_run_flags(parser: argparse.ArgumentParser, args) -> None:
    if args.command != "run":
        return
    if args.report_only and not args.lint_fix:
        parser.error("--report-only only makes sense with --lint-fix")
    if args.lint_fix:
        _validate_lint_fix_flags(parser, args)
    else:
        _validate_build_flags(parser, args)


def _should_use_worktree(args) -> bool:
    if args.command == "plan":
        return not args.no_worktree
    return _is_plain_build(args) and not args.no_worktree


def _resolve_input_path(path: str | None, working_dir: Path) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else working_dir / candidate).resolve()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_run_parser(subparsers)
    _add_review_parser(subparsers)
    _add_plan_parser(subparsers)
    add_native_parser(subparsers)

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
            "worktree branch -- today's `meow issue`."
        ),
    )
    run_parser.add_argument(
        "--lint-fix",
        dest="lint_fix",
        action="store_true",
        help=(
            "Run every configured lint command and fix what it finds -- "
            "today's `meow lint-fix`."
        ),
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
            "prompt, --jira, --gitlab, --branch, or a plan file."
        ),
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
        "--plan", "--plan-file", "-p",
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
        "--review-file", "-r",
        dest="review_file",
        default=None,
        help=(
            "Resume fixing this existing review file instead of running a "
            "fresh review (auto-detects its flavor; implies --fix)."
        ),
    )
    review_parser.add_argument(
        "--no-worktree", "--noworktree", "-n",
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


def _dispatch_lint_fix(args, working_dir: Path) -> None:
    asyncio.run(run_lint_fix(working_dir, report_only=args.report_only))


def _dispatch_jira_build(args, working_dir: Path) -> None:
    approve_plan = _prompt_plan_approval if args.manually_approve_plan else None
    try:
        result = asyncio.run(
            run_issue_solver(working_dir, args.jira or None, approve_plan=approve_plan)
        )
    except IssueUnresolvedError as exc:
        print(f"\nWARNING: could not resolve the issue: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result))


def _dispatch_plain_build(args, working_dir: Path, *, use_worktree: bool) -> None:
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


def _dispatch_run(args, working_dir: Path, *, use_worktree: bool) -> None:
    if args.lint_fix:
        _dispatch_lint_fix(args, working_dir)
    elif args.jira is not None:
        _dispatch_jira_build(args, working_dir)
    else:
        _dispatch_plain_build(args, working_dir, use_worktree=use_worktree)


def _dispatch_review(args, working_dir: Path) -> None:
    plan_file = _resolve_input_path(args.plan, working_dir)
    review_file = _resolve_input_path(args.review_file, working_dir)
    try:
        asyncio.run(
            run_review_command(
                working_dir,
                args.request,
                fix=args.fix,
                jira_key=args.jira,
                gitlab_link=args.gitlab,
                branch=args.branch,
                target=args.target,
                plan_file=plan_file,
                review_file=review_file,
                use_worktree=not args.no_worktree,
            )
        )
    except ValueError as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _dispatch(args, working_dir: Path, *, use_worktree: bool) -> None:
    if args.command == "run":
        _dispatch_run(args, working_dir, use_worktree=use_worktree)
        return
    if args.command == "review":
        _dispatch_review(args, working_dir)
        return
    asyncio.run(
        run_plan(
            working_dir,
            args.feature_name,
            args.request,
            use_worktree=use_worktree,
            source_branch=args.source_branch,
        )
    )


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


def _check_clean_tree(args, working_dir: Path) -> None:
    if not _requires_clean_tree(args):
        return
    try:
        _ensure_clean_tree(working_dir)
    except DirtyWorkingTreeError as exc:
        logger.warning("run_blocked_uncommitted_changes")
        print(f"\nWARNING: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    logger.info("run_initialized", command=args.command)


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


def cli_main():
    configure_logging()
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.command == "native":
        run_native(args)
        return
    working_dir = Path(args.working_dir).resolve()
    log_working_directory(working_dir)
    use_worktree = _should_use_worktree(args)

    _validate_feature_name_requirement(parser, args)
    _validate_run_flags(parser, args)
    _check_clean_tree(args, working_dir)
    _boot_repo(
        working_dir,
        include_gitignore=_creates_a_worktree(args, use_worktree=use_worktree),
    )
    _dispatch(args, working_dir, use_worktree=use_worktree)


if __name__ == "__main__":
    cli_main()
