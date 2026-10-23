"""Read-only rendering of saved run checkpoints."""

import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from meow.execution.orchestration.model import (
    TERMINAL_PHASES as ORCHESTRATOR_TERMINAL_PHASES,
)
from meow.execution.orchestration.model import (
    is_orchestrator_id,
)
from meow.execution.orchestration.report import render_status, rollup_line
from meow.execution.orchestration.store import OrchestratorStateError, OrchestratorStore
from meow.execution.run_state import (
    TERMINAL_PHASES,
    RunRecord,
    RunStateError,
    RunStore,
)
from meow.infrastructure.background import inspect_worker, reconcile_worker
from meow.infrastructure.quality import Concern, QualityStoreError, concerns_for_run
from meow.infrastructure.usage import usage_totals

# Sources `meow resume --continue` can drive; others are rerun from scratch.
RESUMABLE_SOURCES = frozenset({"prompt", "jira", "native", "orchestrator"})


def recovery_command(record: RunRecord) -> str:
    if record.phase == "complete":
        return "Run complete"
    if record.source not in RESUMABLE_SOURCES:
        return f"Rerun the original meow {record.source} command"
    command = f"meow resume {record.id}"
    if record.phase == "needs_user_decision":
        command += ' --continue --request "<intended outcome>"'
    return f'{command} --work-dir "{record.repo}"'


def _gate_lines(  # ruff: ignore[complex-structure, too-many-branches]
    results: dict,
) -> list[str]:
    if "checks" not in results:
        return []
    checks = results.get("checks", [])
    if not isinstance(checks, list):
        return []
    lines = []
    for kind in ("lint", "test", "build", "browser"):
        entries = [
            item
            for item in checks
            if isinstance(item, dict) and item.get("kind") == kind
        ]
        if not entries:
            lines.append(f"{kind.title()}: not configured")
            continue
        if any(item.get("timed_out") for item in entries):
            outcome = "TIMEOUT"
        elif any(item.get("exit_code") not in {0, None} for item in entries):
            outcome = "FAIL"
        elif any(item.get("exit_code") is None for item in entries):
            outcome = "UNAVAILABLE"
        else:
            outcome = "PASS"
        advisory = (
            " (advisory)" if all(not item.get("required") for item in entries) else ""
        )
        lines.append(f"{kind.title()}: {outcome}{advisory}")
    return lines


def _decision_lines(record: RunRecord) -> list[str]:
    if record.phase != "needs_user_decision":
        return []
    decision = record.results.get("preplan", {}).get("decision", {})
    return [
        f"Request: {record.request}",
        *(f"Decision needed: {reason}" for reason in decision.get("reasons", [])),
    ]


def _pipeline_lines(delivery: dict) -> list[str]:
    """The final-delivery outcome: pipeline status and the opened MR."""
    pipeline = delivery.get("pipeline") if isinstance(delivery, dict) else None
    if not isinstance(pipeline, dict) or not pipeline.get("status"):
        return []
    line = "Pipeline: " + str(pipeline["status"]).replace("_", " ")
    if pipeline.get("detail"):
        line += f" ({pipeline['detail']})"
    pr = delivery.get("pr")
    url = pr.get("url") if isinstance(pr, dict) else None
    return [line, *([f"Merge request: {url}"] if url else [])]


def render(  # ruff: ignore[complex-structure, too-many-statements, too-many-branches, too-many-locals]
    record: RunRecord, *, verbose: bool = False, concerns: tuple[Concern, ...] = ()
) -> str:
    started = datetime.fromisoformat(record.created_at)
    elapsed = max(0, int((datetime.now(UTC) - started).total_seconds()))
    totals = usage_totals(record.usage)

    def available(value: object) -> str:
        return str(value) if value is not None else "unavailable"

    updated = datetime.fromisoformat(record.updated_at)
    idle = max(0, int((datetime.now(UTC) - updated).total_seconds()))
    entries = record.usage.get("entries", []) if isinstance(record.usage, dict) else []
    stamps = [e["at"] for e in entries if isinstance(e, dict) and e.get("at")]
    usage_note = ""
    if record.phase not in TERMINAL_PHASES:
        usage_note = (
            f" (as of {stamps[-1]}; later turns not yet reported)"
            if stamps
            else " (no SDK result reported yet)"
        )
    tasks_error = next(
        (
            f"{task_id}: {detail['error']}"
            for task_id, detail in (record.results.get("tasks") or {}).items()
            if isinstance(detail, dict) and detail.get("error")
        ),
        None,
    )
    reviewer = record.results.get("reviewer", "unavailable")
    lines = [
        f"Run: {record.id}",
        f"Phase: {record.phase}",
        f"Elapsed: {elapsed}s",
        f"Last checkpoint: {idle}s ago",
        f"Turns: {available(totals['turns'])}",
        f"Tokens: {available(totals['tokens'])}{usage_note}",
        f"Cost (USD): {available(totals['cost_usd'])}{usage_note}",
        f"Last result/failure: {record.last_failure or tasks_error or reviewer}",
        f"Worktree: {record.worktree}",
        f"Branch: {record.branch}",
        f"Recovery: {recovery_command(record)}",
    ]
    lines.extend(_decision_lines(record))
    if record.delivery.get("base"):
        lines.append(f"MR target: {record.delivery['base']}")
    worker = inspect_worker(record)
    workflow = record.results.get("workflow")
    if workflow:
        lines.append(f"Workflow: {workflow['name']} ({workflow['scope']})")
    lines.extend(
        f"Stage {step_id}: {detail['status']}"
        for step_id, detail in record.results.get("stages", {}).items()
    )
    if worker.state != "not_background":
        lines.append(f"Background worker: {worker.state}")
        lines.append(f"Worker log: {worker.log or 'unavailable'}")
        if worker.pid is not None:
            lines.append(f"Worker PID: {worker.pid}")
    lines.extend(_gate_lines(record.results))
    lines.extend(_pipeline_lines(record.delivery))
    lines.extend(f"Quality concern: {item.path} — {item.impact}" for item in concerns)
    tasks = record.results.get("tasks")
    if isinstance(tasks, dict) and tasks:
        counts = Counter(
            item.get("status", "unknown")
            for item in tasks.values()
            if isinstance(item, dict)
        )
        summary = ", ".join(
            f"{counts[state]} {state}"
            for state in (
                "complete",
                "running",
                "waiting",
                "failed",
                "skipped",
                "cancelled",
            )
            if counts[state]
        )
        lines.append(f"Tasks: {summary or 'unavailable'}")
        if verbose:
            lines.extend(
                f"  {task_id}: {detail.get('status', 'unknown')} "
                f"owns {detail.get('owned_paths', [])}"
                + (f" error: {detail['error']}" if detail.get("error") else "")
                for task_id, detail in tasks.items()
                if isinstance(detail, dict)
            )
    if verbose:
        lines.extend(
            f"  Evidence: {item.evidence}; follow-up: {item.follow_up}"
            for item in concerns
        )
        lines.extend([
            "Transitions:",
            *(
                f"  {item['at']} {item['phase']} round={item.get('round', 0)}"
                for item in record.transitions
            ),
        ])
        lines.append(f"Results: {record.results or 'unavailable'}")
        entries = (
            record.usage.get("entries", []) if isinstance(record.usage, dict) else []
        )
        lines.append("SDK usage by role:")
        lines.extend(f"  {entry}" for entry in entries)
        if not entries:
            lines.append("  unavailable")
    return "\n".join(lines)


def active_orchestrator_lines(working_dir: Path) -> list[str]:
    """One rollup line per orchestrator that is still driving work."""
    store = OrchestratorStore(working_dir)
    if not store.directory.is_dir():
        return []
    return [
        rollup_line(record)
        for record in store.records()
        if record.phase not in ORCHESTRATOR_TERMINAL_PHASES
    ]


def saved_run_id(working_dir: Path, run_id: str | None) -> str | None:
    """Resolve the latest execution across both engines, preferring a child's parent."""
    if run_id is not None:
        return run_id
    parents = OrchestratorStore(working_dir).records()
    if not parents:
        return None
    try:
        latest = RunStore(working_dir).latest()
    except RunStateError:
        latest = None
    candidates = list(parents)
    if latest is not None and not any(
        latest.id in task.run_ids for parent in parents for task in parent.tasks
    ):
        candidates.append(latest)
    return max(candidates, key=lambda record: record.updated_at).id


def status(working_dir: Path, run_id: str | None = None, verbose: bool = False) -> int:
    run_id = saved_run_id(working_dir, run_id)
    if run_id is not None and is_orchestrator_id(run_id):
        return _orchestration_status(working_dir, run_id)
    store = RunStore(working_dir)
    rollups = active_orchestrator_lines(working_dir)
    try:
        record = store.load(run_id) if run_id else store.latest()
        if record.background:
            reconcile_worker(store, record.id)
            record = store.load(record.id)
    except RunStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        concerns = tuple(
            concerns_for_run(
                Path(record.repo), record.id, evidence_root=Path(record.worktree)
            )
        )
    except QualityStoreError as exc:
        concerns = ()
        print(f"Quality concerns unavailable: {exc}", file=sys.stderr)
    print(render(record, verbose=verbose, concerns=concerns))
    if rollups:
        print("\n".join(rollups))
    return 0


def _orchestration_status(working_dir: Path, run_id: str) -> int:
    try:
        print(render_status(OrchestratorStore(working_dir).load(run_id)))
    except (OrchestratorStateError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
