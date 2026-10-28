"""Multi-sprint execution and delivery output behind `meow run`."""

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import Coroutine
from pathlib import Path

from meow.cli.validation import _resolve_input_path
from meow.execution.orchestration.intake import IntakeRequest
from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.pull_request import detect_provider, manual_pr_link
from meow.execution.orchestration.report import render_report, render_status
from meow.execution.orchestration.runner import (
    resume_orchestrator,
    start_orchestrator,
)
from meow.execution.orchestration.store import OrchestratorStore
from meow.infrastructure.worktree import (
    DirtyWorkingTreeError,
    _boot_repo,
    _ensure_clean_tree,
    _require_source_worktree,
)
from meow.integrations.issue_solver import _load_jira_config
from meow.project.config import load_config
from meow.project.onboarding import bind_provider_references

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130


def _usage_error(message: str) -> int:
    print(f"meow run: error: {message}", file=sys.stderr)
    return EXIT_USAGE


def _drive[T](coroutine: Coroutine[object, object, T]) -> T:
    try:
        return asyncio.run(coroutine)
    except KeyboardInterrupt:
        print("\nInterrupted; the orchestrator was cancelled.", file=sys.stderr)
        raise SystemExit(EXIT_CANCELLED) from None


def _jira_problem(args: argparse.Namespace, working_dir: Path) -> str | None:
    """Why --jira/--jql cannot run here, or None when they can (or are unused)."""
    if args.jira is None and args.jql is None:
        return None
    try:
        bind_provider_references(working_dir)
        _load_jira_config(load_config(working_dir))
    except (OSError, ValueError) as exc:
        return (
            f"--jira and --jql need [jira] and [jira.mcp] in .meow/config.toml.\n{exc}"
        )
    return None


def _intake(args: argparse.Namespace, tasks_file: Path | None) -> IntakeRequest:
    if args.jira is not None:
        return IntakeRequest(
            "jira",
            jira_keys=tuple(args.jira) if isinstance(args.jira, list) else (args.jira,),
        )
    if args.jql is not None:
        return IntakeRequest("jql", jql=args.jql)
    if tasks_file is not None:
        return IntakeRequest("tasks", tasks_file=tasks_file)
    return IntakeRequest("objective", objective=args.request)


def _refusal(
    args: argparse.Namespace, working_dir: Path, tasks_file: Path | None
) -> int | None:
    """An exit code when this start must be refused, else None."""
    if tasks_file is not None and not tasks_file.is_file():
        return _usage_error("--tasks must name an existing file")
    try:
        _require_source_worktree(working_dir, args.source_branch)
        if not args.source_branch:
            _ensure_clean_tree(working_dir)
    except DirtyWorkingTreeError as exc:
        print(f"\nWARNING: {exc}", file=sys.stderr)
        return EXIT_FAILED
    problem = _jira_problem(args, working_dir)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_FAILED
    return None


def run_orchestration(args: argparse.Namespace, working_dir: Path) -> int:
    tasks_file = _resolve_input_path(args.tasks_file, working_dir)
    refusal = _refusal(args, working_dir, tasks_file)
    if refusal is not None:
        return refusal
    _boot_repo(working_dir, include_gitignore=True)
    orch_id, code = _drive(
        start_orchestrator(
            working_dir,
            _intake(args, tasks_file),
            base_branch=args.source_branch,
            **({"target_branch": args.target_branch} if args.target_branch else {}),
            max_parallel=args.max_parallel,
            test=args.test,
            **({"force_disable_tests": True} if args.force_disable_tests else {}),
            open_pr=False if args.no_pr else None,
            dry_run=args.dry_run,
        )
    )
    return _finish(working_dir, orch_id, code)


async def resume_orchestration(
    working_dir: Path, run_id: str, *, task_id: str | None, request: str | None
) -> int:
    code = await resume_orchestrator(
        working_dir, run_id, task_id=task_id, request=request
    )
    return _finish(working_dir, run_id, code)


def _manual_link(working_dir: Path, record: OrchestratorRecord) -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=working_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    url = result.stdout.strip()
    if result.returncode or not url:
        return None
    provider = detect_provider(url, load_config(working_dir))
    return manual_pr_link(
        url,
        provider,
        record.options.get("target_branch") or record.base_branch,
        record.integration["branch"],
    )


def _print_delivery(working_dir: Path, record: OrchestratorRecord) -> None:
    pr = record.delivery.get("pr")
    if pr is None and record.options.get("pr", True):
        reason = record.delivery.get("pr_error") or "unknown reason"
        print(f"MR not opened: {reason}", file=sys.stderr)
        link = _manual_link(working_dir, record)
        if link:
            print(f"Open it manually: {link}", file=sys.stderr)
    print(
        json.dumps({
            "orchestrator": record.id,
            "branch": record.integration["branch"],
            "pr": pr["url"] if pr else None,
            "tasks": len(record.tasks),
        })
    )


def _print_dry_run(record: OrchestratorRecord) -> None:
    print(
        f"Dry run: orchestrator {record.id} decomposed {len(record.tasks)} "
        "task(s); nothing was executed."
    )
    print(render_status(record))
    print(f"Run it with: meow resume {record.id} --continue")


def _finish(working_dir: Path, orch_id: str, code: int) -> int:
    record = OrchestratorStore(working_dir).load(orch_id)
    if record.intake.get("dry_run") and record.phase == "decomposing":
        _print_dry_run(record)
    elif record.phase == "delivered":
        _print_delivery(working_dir, record)
    elif record.phase in {"blocked", "failed"}:
        print(render_report(working_dir, record))
        if record.error:
            print(f"Error: {record.error.get('message')}", file=sys.stderr)
    else:
        print(f"Orchestrator {record.id} is {record.phase}.")
    return code
