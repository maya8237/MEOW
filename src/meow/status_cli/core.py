"""Read-only rendering of saved run checkpoints."""

import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from meow.background import inspect_worker, reconcile_worker
from meow.quality import Concern, QualityStoreError, concerns_for_run
from meow.run_state import RunRecord, RunStateError, RunStore
from meow.usage import usage_totals


def recovery_command(record: RunRecord) -> str:
    if record.phase == "complete":
        return "Run complete"
    return f'meow resume {record.id} --working-dir "{record.repo}"'


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


def render(  # ruff: ignore[complex-structure, too-many-statements]
    record: RunRecord, *, verbose: bool = False, concerns: tuple[Concern, ...] = ()
) -> str:
    started = datetime.fromisoformat(record.created_at)
    elapsed = max(0, int((datetime.now(UTC) - started).total_seconds()))
    totals = usage_totals(record.usage)

    def available(value: object) -> str:
        return str(value) if value is not None else "unavailable"

    lines = [
        f"Run: {record.id}",
        f"Phase: {record.phase}",
        f"Elapsed: {elapsed}s",
        f"Turns: {available(totals['turns'])}",
        f"Tokens: {available(totals['tokens'])}",
        f"Cost (USD): {available(totals['cost_usd'])}",
        "Last result/failure: "
        f"{record.last_failure or record.results.get('reviewer', 'unavailable')}",
        f"Worktree: {record.worktree}",
        f"Branch: {record.branch}",
        f"Recovery: {recovery_command(record)}",
    ]
    worker = inspect_worker(record)
    if worker.state != "not_background":
        lines.append(f"Background worker: {worker.state}")
        lines.append(f"Worker log: {worker.log or 'unavailable'}")
        if worker.pid is not None:
            lines.append(f"Worker PID: {worker.pid}")
    lines.extend(_gate_lines(record.results))
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
            for state in ("complete", "running", "waiting", "failed", "cancelled")
            if counts[state]
        )
        lines.append(f"Tasks: {summary or 'unavailable'}")
        if verbose:
            lines.extend(
                f"  {task_id}: {detail.get('status', 'unknown')} "
                f"owns {detail.get('owned_paths', [])}"
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


def status(working_dir: Path, run_id: str | None = None, verbose: bool = False) -> int:
    store = RunStore(working_dir)
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
    return 0
