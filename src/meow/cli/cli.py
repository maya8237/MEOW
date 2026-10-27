"""CLI entry point for the meow harness."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from claude_agent_sdk import ProcessError

from meow.cli.catalog_cli import catalog_command
from meow.cli.cleanup_cli import cleanup_command
from meow.cli.dispatch import _dispatch
from meow.cli.ipython_cli import start_ipython
from meow.cli.orchestration_cli import run_orchestration
from meow.cli.parser import _build_arg_parser, expand_shortcuts
from meow.cli.resume_cli import resume, resume_parked
from meow.cli.skills_cli import skills_command
from meow.cli.status_cli import status
from meow.cli.validation import (
    _resolve_input_path,
    _validate_ci_flags,
    _validate_feature_name_requirement,
    _validate_run_flags,
    validate_workflow_args,
)
from meow.evaluation import evaluate_run
from meow.execution.orchestration.cleanup import clean_orchestrator
from meow.execution.orchestration.model import is_orchestrator_id
from meow.execution.orchestration.runner import cancel_orchestrator
from meow.execution.orchestration.store import OrchestratorStateError
from meow.execution.orchestrator import log_working_directory
from meow.execution.run_policy import (
    _creates_a_worktree,
    _should_use_worktree,
    check_clean_tree,
    uses_orchestration,
)
from meow.execution.run_state import RunStateError, RunStore, reconcile_quietly
from meow.infrastructure.background import (
    BackgroundError,
    drive_main,
    launch_background,
    worker_main,
)
from meow.infrastructure.cancellation import request_cancel
from meow.infrastructure.logging import configure_logging, get_logger
from meow.infrastructure.supervisor import run_supervised, should_supervise
from meow.infrastructure.worktree import (
    DirtyWorkingTreeError,
    _boot_repo,
)
from meow.infrastructure.worktree_controls import (
    WorktreeSafetyError,
    clean_worktree,
    inspect_worktree,
    list_worktrees,
)
from meow.integrations.ci_review import CiReviewError, run_ci_review
from meow.integrations.docs_update import (
    prepare_docs_update,
    run_docs_update,
)
from meow.native.native_cli import run_native
from meow.project.config import load_config
from meow.project.onboarding import onboard_if_needed
from meow.ui import serve_ui

logger = get_logger(__name__)

# Commands that read or drive saved runs: each first parks runs whose driver
# died without settling them (a killed foreground run, a crashed worker).
_RECONCILE_COMMANDS = frozenset({
    "status",
    "resume",
    "run",
    "queue",
    "cancel",
    "plan",
    "review",
    "worktree",
    "cleanup",
})


# Anything a run can raise on a misbehaving environment: I/O, timeouts (also an
# OSError), failed git/subprocess calls, and groups from parallel workflow
# branches. Programming errors (TypeError, KeyError, ...) keep their traceback.
_EXPECTED_FAILURES = (
    ProcessError,
    ValueError,
    RuntimeError,
    OSError,
    subprocess.SubprocessError,
    ExceptionGroup,
)


def _describe_failure(exc: BaseException) -> str:
    """One readable message; a group is flattened to its leaf failures."""
    if isinstance(exc, BaseExceptionGroup):
        return "\n".join(_describe_failure(item) for item in exc.exceptions)
    if isinstance(exc, subprocess.CalledProcessError):
        command = " ".join(str(part) for part in exc.cmd)
        detail = str(exc.stderr or exc.output or "").strip()
        return f"Command failed (exit {exc.returncode}): {command}" + (
            f"\n{detail}" if detail else ""
        )
    return str(exc) or type(exc).__name__


def _check_clean_tree(args, working_dir: Path) -> None:
    try:
        check_clean_tree(args, working_dir)
    except DirtyWorkingTreeError as exc:
        print(f"\nWARNING: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cli_main(argv=None):  # ruff: ignore[too-many-statements, too-many-return-statements, too-many-locals] -- command dispatch
    configure_logging()
    from meow.installer.maintenance import check_for_updates

    effective_argv = expand_shortcuts(list(sys.argv[1:] if argv is None else argv))
    first = effective_argv[0] if effective_argv else "ipython"
    if first not in {
        "-u",
        "--update",
        "--check-update",
    } and not first.startswith("_"):
        check_for_updates()
    if argv is None and len(sys.argv) == 1:
        start_ipython()
        return
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.check_update:
        check_for_updates()
        return
    if args.update:
        from meow.installer.maintenance import update

        try:
            raise SystemExit(update())
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.CalledProcessError,
        ) as exc:
            print(f"Update failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        except KeyboardInterrupt:
            raise SystemExit(130) from None
    if args.command == "_orchestrator-task":
        parser.error("_orchestrator-task is internal")
    if args.command == "ipython":
        start_ipython()
        return
    if args.command == "native":
        run_native(args)
        return
    working_dir = Path(args.working_dir).resolve()
    log_working_directory(working_dir)
    if args.command in _RECONCILE_COMMANDS:
        reconcile_quietly(working_dir)
    if args.command == "cleanup":
        cleanup_command(working_dir, args.run_id)
        return
    if args.command in {"schedule", "_scheduled"}:
        from meow.cli.schedule_cli import schedule_command
        from meow.infrastructure.scheduling import execute_schedule

        try:
            if args.command == "_scheduled":
                raise SystemExit(execute_schedule(working_dir, args.schedule_id))
            schedule_command(args, working_dir)
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"Schedule failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return
    if args.command in {"workflows", "agents"}:
        try:
            catalog_command(args, working_dir)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return
    if args.command == "skills":
        try:
            skills_command(args, working_dir)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return
    if args.command == "ui":
        try:
            raise SystemExit(
                serve_ui(working_dir, args.port, open_browser=not args.no_browser)
            )
        except OSError as exc:
            print(f"Could not start the catalog page: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    if args.command == "detach":
        from meow.infrastructure.background import detach_command

        try:
            info = detach_command(working_dir, args.detach_argv, args.log)
        except (BackgroundError, OSError) as exc:
            print(f"Detach failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(info))
        return
    if args.command == "_worker":
        raise SystemExit(worker_main(working_dir, args.run_id, args.nonce))
    if args.command == "_drive":
        raise SystemExit(drive_main(working_dir, args.run_id, args.nonce))
    if args.command == "status":
        raise SystemExit(status(working_dir, args.run_id, args.verbose))
    if args.command == "cancel":
        try:
            if is_orchestrator_id(args.run_id):
                cancel_orchestrator(working_dir, args.run_id)
            else:
                request_cancel(RunStore(working_dir), args.run_id)
        except (RunStateError, ValueError, OSError, TimeoutError) as exc:
            print(f"Cancel failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Cancellation requested for {args.run_id}.")
        return
    if args.command == "worktree":
        try:
            if args.worktree_command == "list":
                inspections = list_worktrees(working_dir)
                for item in inspections:
                    print(f"{item.run_id} {item.phase} {item.path}")
                if not inspections:
                    print("No saved run worktrees.")
            elif args.worktree_command == "clean" and is_orchestrator_id(args.run_id):
                removed = clean_orchestrator(working_dir, args.run_id)
                print(
                    json.dumps({
                        "orchestrator": args.run_id,
                        "removed": [str(path) for path in removed],
                    })
                )
            else:
                item = (
                    inspect_worktree(working_dir, args.run_id)
                    if args.worktree_command == "inspect"
                    else clean_worktree(working_dir, args.run_id)
                )
                payload = asdict(item)
                payload["path"] = str(item.path)
                print(json.dumps(payload))
        except (
            WorktreeSafetyError,
            RunStateError,
            OrchestratorStateError,
            OSError,
        ) as exc:
            print(f"Worktree {args.worktree_command} failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return
    if args.command == "evaluate":
        try:
            report = evaluate_run(working_dir, args.run_id, tuple(args.compare))
        except (ValueError, OSError) as exc:
            print(f"Evaluation unavailable: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(report.render(args.verbose))
        return
    if args.command == "resume" and args.parked:
        if args.run_id or args.continue_run or args.task_id or args.request:
            parser.error("--parked takes no run ID, --continue, --task or --request")
        try:
            raise SystemExit(asyncio.run(resume_parked(working_dir)))
        except KeyboardInterrupt:
            raise SystemExit(130) from None
    if args.command == "resume":
        try:
            code = asyncio.run(
                resume(
                    working_dir,
                    args.run_id,
                    continue_run=args.continue_run,
                    request=args.request,
                    repair=args.repair,
                    **({"task_id": args.task_id} if args.task_id is not None else {}),
                )
            )
        except _EXPECTED_FAILURES as exc:
            print(f"\n{_describe_failure(exc)}", file=sys.stderr)
            raise SystemExit(1) from exc
        raise SystemExit(code)
    if uses_orchestration(args):
        _validate_run_flags(parser, args)
        try:
            code = run_orchestration(args, working_dir)
        except _EXPECTED_FAILURES as exc:
            print(f"\n{_describe_failure(exc)}", file=sys.stderr)
            raise SystemExit(1) from exc
        raise SystemExit(code)
    if args.command == "docs-update":
        try:
            target = load_config(working_dir)["delivery"]["target_branch"]
            prepared = prepare_docs_update(working_dir, args.since, target)
            result = asyncio.run(run_docs_update(prepared))
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"docs-update failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Baseline: {result.baseline_sha}")
        print(f"Inspected HEAD: {result.head_sha}")
        print("Edited files: " + (", ".join(result.changed_paths) or "none"))
        print(result.diff or "No documentation changes.")
        return
    if args.command == "review":
        _validate_ci_flags(parser, args)
        if args.ci:
            plan_file = _resolve_input_path(args.plan, working_dir)
            if plan_file is not None and not plan_file.is_file():
                parser.error("--plan must name an existing file")
            artifact_dir = _resolve_input_path(
                args.artifact_dir or ".meow/ci-artifacts", working_dir
            )
            try:
                onboard_if_needed(working_dir, working_dir)
                config = load_config(working_dir)
                if args.force_disable_tests:
                    from meow.infrastructure.verification_policy import disable_tests

                    print(
                        "Tests: SKIPPED (--force-disable-tests): "
                        + ", ".join(disable_tests(config))
                    )
                target = config["delivery"]["target_branch"]
                result = run_ci_review(
                    working_dir,
                    config,
                    os.environ,
                    args.target_ref or f"refs/remotes/origin/{target}",
                    artifact_dir,
                    plan_file,
                    post_comments=args.post_comments,
                )
            except (CiReviewError, ValueError, OSError) as exc:
                print(f"CI review failed: {exc}", file=sys.stderr)
                raise SystemExit(2) from exc
            print(f"CI review {result.verdict}: {result.report_path}")
            raise SystemExit(result.exit_code)
    use_worktree = _should_use_worktree(args)

    _validate_feature_name_requirement(parser, args, working_dir)
    _validate_run_flags(parser, args)
    try:
        validate_workflow_args(args, working_dir)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    _check_clean_tree(args, working_dir)
    _boot_repo(
        working_dir,
        include_gitignore=_creates_a_worktree(args, use_worktree=use_worktree),
    )
    if args.command == "run" and args.background:
        try:
            run_id = launch_background(
                working_dir, sys.argv[1:] if argv is None else list(argv)
            )
        except BackgroundError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Background run: {run_id}")
        print(f"Inspect: meow status {run_id}")
        return
    if (
        args.command == "run"
        and not args.lint_fix
        and not args.manually_approve_plan
        and should_supervise(working_dir)
    ):
        try:
            code = run_supervised(
                working_dir, sys.argv[1:] if argv is None else list(argv)
            )
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except (*_EXPECTED_FAILURES, BackgroundError) as exc:
            print(f"\n{_describe_failure(exc)}", file=sys.stderr)
            raise SystemExit(1) from exc
        raise SystemExit(code)
    try:
        _dispatch(args, working_dir, use_worktree=use_worktree)
    except _EXPECTED_FAILURES as exc:
        # The last-resort net: a handful of specific, expected failures
        # (DirtyWorkingTreeError, PlanNotApprovedError, IssueUnresolvedError,
        # review's own ValueError) are already caught closer to their source
        # with a more specific message. Anything else that reaches here --
        # a missing or malformed MEOW config, a worktree/git failure, a
        # review file in a flavor that can't be resumed, no plan file to
        # resume a review at, ... -- would otherwise surface as a raw
        # traceback instead of the clear, one-line error every other
        # failure in this CLI gets. FileNotFoundError is here specifically
        # for a missing MEOW config/plan file -- far and away the most
        # likely first mistake a new user makes.
        print(f"\n{_describe_failure(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli_main()
