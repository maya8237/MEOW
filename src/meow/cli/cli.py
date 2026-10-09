"""CLI entry point for the meow harness."""

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from meow.cli.dispatch import _dispatch
from meow.cli.ipython_cli import start_ipython
from meow.cli.parser import _build_arg_parser
from meow.cli.resume_cli import resume
from meow.cli.status_cli import status
from meow.cli.validation import (
    _resolve_input_path,
    _validate_ci_flags,
    _validate_feature_name_requirement,
    _validate_run_flags,
)
from meow.evaluation import evaluate_run
from meow.execution.orchestrator import log_working_directory
from meow.execution.run_policy import (
    _creates_a_worktree,
    _should_use_worktree,
    check_clean_tree,
)
from meow.execution.run_state import RunStateError, RunStore
from meow.infrastructure.background import (
    BackgroundError,
    launch_background,
    worker_main,
)
from meow.infrastructure.cancellation import request_cancel
from meow.infrastructure.logging import configure_logging, get_logger
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
    DocsUpdateError,
    prepare_docs_update,
    run_docs_update,
)
from meow.native.native_cli import run_native
from meow.project.config import load_config

logger = get_logger(__name__)


def _check_clean_tree(args, working_dir: Path) -> None:
    try:
        check_clean_tree(args, working_dir)
    except DirtyWorkingTreeError as exc:
        print(f"\nWARNING: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cli_main(argv=None):  # ruff: ignore[too-many-statements, too-many-return-statements] -- command dispatch
    configure_logging()
    if argv is None and len(sys.argv) == 1:
        start_ipython()
        return
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "ipython":
        start_ipython()
        return
    if args.command == "native":
        run_native(args)
        return
    working_dir = Path(args.working_dir).resolve()
    log_working_directory(working_dir)
    if args.command == "_worker":
        raise SystemExit(worker_main(working_dir, args.run_id, args.nonce))
    if args.command == "status":
        raise SystemExit(status(working_dir, args.run_id, args.verbose))
    if args.command == "cancel":
        try:
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
            else:
                item = (
                    inspect_worktree(working_dir, args.run_id)
                    if args.worktree_command == "inspect"
                    else clean_worktree(working_dir, args.run_id)
                )
                payload = asdict(item)
                payload["path"] = str(item.path)
                print(json.dumps(payload))
        except (WorktreeSafetyError, RunStateError, OSError) as exc:
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
    if args.command == "resume":
        raise SystemExit(
            asyncio.run(
                resume(
                    working_dir,
                    args.run_id,
                    continue_run=args.continue_run,
                    auto_resume=args.auto_resume,
                )
            )
        )
    if args.command == "docs-update":
        try:
            prepared = prepare_docs_update(working_dir, args.since)
            result = asyncio.run(run_docs_update(prepared))
        except (DocsUpdateError, RuntimeError, OSError) as exc:
            print(f"docs-update failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Baseline: {result.baseline_sha}")
        print(f"Inspected HEAD: {result.head_sha}")
        print("Edited files: " + (", ".join(result.changed_paths) or "none"))
        print(result.diff or "No documentation changes.")
        return
    if args.command in {"knowledge", "shape", "hooks"}:
        _dispatch(args, working_dir, use_worktree=False)
        return
    if args.command == "review":
        _validate_ci_flags(parser, args)
        if args.ci:
            plan_file = _resolve_input_path(args.plan, working_dir)
            if plan_file is not None and not plan_file.is_file():
                parser.error("--plan-file must name an existing file")
            artifact_dir = _resolve_input_path(
                args.artifact_dir or ".meow/ci-artifacts", working_dir
            )
            try:
                result = run_ci_review(
                    working_dir,
                    load_config(working_dir),
                    os.environ,
                    args.target_ref or "refs/remotes/origin/dev",
                    artifact_dir,
                    plan_file,
                )
            except (CiReviewError, ValueError, OSError) as exc:
                print(f"CI review failed: {exc}", file=sys.stderr)
                raise SystemExit(2) from exc
            print(f"CI review {result.verdict}: {result.report_path}")
            raise SystemExit(result.exit_code)
    use_worktree = _should_use_worktree(args)

    _validate_feature_name_requirement(parser, args, working_dir)
    _validate_run_flags(parser, args)
    _check_clean_tree(args, working_dir)
    _boot_repo(
        working_dir,
        include_gitignore=_creates_a_worktree(args, use_worktree=use_worktree),
    )
    if args.command == "run" and args.background:
        try:
            run_id = launch_background(working_dir, sys.argv[1:])
        except BackgroundError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Background run: {run_id}")
        print(f"Inspect: meow status {run_id}")
        return
    try:
        _dispatch(args, working_dir, use_worktree=use_worktree)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
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
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli_main()
