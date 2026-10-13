"""`meow orchestrate`: argument rules, dispatch, and the printed results."""

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path

from meow.cli.validation import _resolve_input_path
from meow.execution.orchestration.intake import IntakeRequest
from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.pull_request import detect_provider, manual_pr_link
from meow.execution.orchestration.report import render_report, render_status
from meow.execution.orchestration.runner import (
    cancel_orchestrator,
    resume_orchestrator,
    start_orchestrator,
)
from meow.execution.orchestration.store import OrchestratorStore
from meow.infrastructure.worktree import (
    DirtyWorkingTreeError,
    _boot_repo,
    _ensure_clean_tree,
)
from meow.integrations.issue_solver import _load_jira_config
from meow.project.config import load_config

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130
ACTIONS = ("status", "resume", "cancel")

# Flags that choose where the work comes from.
_SOURCE_FLAGS = (
    ("--jira", lambda a: a.jira is not None),
    ("--jql", lambda a: a.jql is not None),
    ("--tasks", lambda a: a.tasks_file is not None),
)
# Flags that only mean something when an orchestrator is started.
_START_FLAGS = (
    ("--from", lambda a: a.source_branch is not None),
    ("--max-parallel", lambda a: a.max_parallel is not None),
    ("--dry-run", lambda a: a.dry_run),
    ("--test", lambda a: a.test),
    ("--no-pr", lambda a: a.no_pr),
)


class UsageError(ValueError):
    """The command line is invalid; reported like an argparse error (exit 2)."""


@dataclass(frozen=True)
class Invocation:
    action: str  # "start", "status", "resume", or "cancel"
    orch_id: str | None = None
    objective: str | None = None


def _reject_resume_flags(args: argparse.Namespace) -> None:
    if args.task_id is not None or args.request is not None:
        raise UsageError("--task and --request only apply to `orchestrate resume`")


def _classify_action(
    args: argparse.Namespace, action: str, rest: list[str]
) -> Invocation:
    offending = [
        flag for flag, is_set in (*_SOURCE_FLAGS, *_START_FLAGS) if is_set(args)
    ]
    if offending:
        raise UsageError(
            f"{', '.join(offending)} cannot be used with `orchestrate {action}`"
        )
    if action != "resume":
        _reject_resume_flags(args)
    if action == "status" and len(rest) > 1:
        raise UsageError("`orchestrate status` takes at most one orchestrator ID")
    if action != "status" and len(rest) != 1:
        raise UsageError(f"`orchestrate {action}` needs exactly one orchestrator ID")
    return Invocation(action, orch_id=rest[0] if rest else None)


def classify(args: argparse.Namespace) -> Invocation:
    """Decide what the command line asks for, or raise `UsageError`."""
    words = list(args.words)
    if words and words[0] in ACTIONS:
        return _classify_action(args, words[0], words[1:])
    _reject_resume_flags(args)
    objective = " ".join(words).strip()
    given = [bool(objective), *(is_set(args) for _, is_set in _SOURCE_FLAGS)]
    if sum(given) != 1:
        raise UsageError(
            "give exactly one of: an objective, --jira KEY ..., --jql QUERY, "
            "--tasks FILE"
        )
    return Invocation("start", objective=objective or None)


def _usage_error(message: str) -> int:
    print(f"meow orchestrate: error: {message}", file=sys.stderr)
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
        _load_jira_config(load_config(working_dir))
    except (OSError, ValueError) as exc:
        return (
            f"--jira and --jql need [jira] and [jira.mcp] in .meow/config.toml.\n{exc}"
        )
    return None


def _intake(
    args: argparse.Namespace, invocation: Invocation, tasks_file: Path | None
) -> IntakeRequest:
    if args.jira is not None:
        return IntakeRequest("jira", jira_keys=tuple(args.jira))
    if args.jql is not None:
        return IntakeRequest("jql", jql=args.jql)
    if tasks_file is not None:
        return IntakeRequest("tasks", tasks_file=tasks_file)
    return IntakeRequest("objective", objective=invocation.objective)


def _refusal(
    args: argparse.Namespace, working_dir: Path, tasks_file: Path | None
) -> int | None:
    """An exit code when this start must be refused, else None."""
    if tasks_file is not None and not tasks_file.is_file():
        return _usage_error("--tasks must name an existing file")
    problem = _jira_problem(args, working_dir)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_FAILED
    try:
        _ensure_clean_tree(working_dir)
    except DirtyWorkingTreeError as exc:
        print(f"\nWARNING: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return None


def _start(args: argparse.Namespace, working_dir: Path, invocation: Invocation) -> int:
    tasks_file = _resolve_input_path(args.tasks_file, working_dir)
    refusal = _refusal(args, working_dir, tasks_file)
    if refusal is not None:
        return refusal
    _boot_repo(working_dir, include_gitignore=True)
    orch_id, code = _drive(
        start_orchestrator(
            working_dir,
            _intake(args, invocation, tasks_file),
            base_branch=args.source_branch,
            max_parallel=args.max_parallel,
            test=args.test,
            open_pr=False if args.no_pr else None,
            dry_run=args.dry_run,
        )
    )
    return _finish(working_dir, orch_id, code)


def _resume(args: argparse.Namespace, working_dir: Path, invocation: Invocation) -> int:
    code = _drive(
        resume_orchestrator(
            working_dir,
            invocation.orch_id,
            task_id=args.task_id,
            request=args.request,
        )
    )
    return _finish(working_dir, invocation.orch_id, code)


def _cancel(args: argparse.Namespace, working_dir: Path, invocation: Invocation) -> int:
    record = cancel_orchestrator(working_dir, invocation.orch_id)
    print(
        f"Cancellation requested for orchestrator {record.id}; phase: {record.phase}."
    )
    return 0


def _status(args: argparse.Namespace, working_dir: Path, invocation: Invocation) -> int:
    store = OrchestratorStore(working_dir)
    try:
        record = (
            store.load(invocation.orch_id) if invocation.orch_id else store.latest()
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED
    print(render_status(record))
    return 0


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
        url, provider, record.base_branch, record.integration["branch"]
    )


def _print_delivery(working_dir: Path, record: OrchestratorRecord) -> None:
    pr = record.delivery.get("pr")
    if pr is None and record.options.get("pr", True):
        reason = record.delivery.get("pr_error") or "unknown reason"
        print(f"PR not opened: {reason}", file=sys.stderr)
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
    print(f"Run it with: meow orchestrate resume {record.id}")


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


_HANDLERS = {
    "start": _start,
    "status": _status,
    "resume": _resume,
    "cancel": _cancel,
}


def orchestrate_command(args: argparse.Namespace, working_dir: Path) -> int:
    """Run one `meow orchestrate` invocation and return its exit code."""
    try:
        invocation = classify(args)
    except UsageError as exc:
        return _usage_error(str(exc))
    return _HANDLERS[invocation.action](args, working_dir, invocation)
