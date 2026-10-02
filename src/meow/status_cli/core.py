"""Read-only rendering of saved run checkpoints."""

import sys
from datetime import UTC, datetime
from pathlib import Path

from meow.run_state import RunRecord, RunStateError, RunStore


def recovery_command(record: RunRecord) -> str:
    if record.phase == "complete":
        return "Run complete"
    return f'meow resume {record.id} --working-dir "{record.repo}"'


def render(record: RunRecord, *, verbose: bool = False) -> str:
    started = datetime.fromisoformat(record.created_at)
    elapsed = max(0, int((datetime.now(UTC) - started).total_seconds()))
    lines = [
        f"Run: {record.id}",
        f"Phase: {record.phase}",
        f"Elapsed: {elapsed}s",
        "Last result/failure: "
        f"{record.last_failure or record.results.get('reviewer', 'unavailable')}",
        f"Worktree: {record.worktree}",
        f"Branch: {record.branch}",
        f"Recovery: {recovery_command(record)}",
    ]
    if verbose:
        lines.extend([
            "Transitions:",
            *(
                f"  {item['at']} {item['phase']} round={item.get('round', 0)}"
                for item in record.transitions
            ),
        ])
        lines.append(f"Results: {record.results or 'unavailable'}")
        lines.append(f"SDK usage: {record.usage or 'unavailable'}")
    return "\n".join(lines)


def status(working_dir: Path, run_id: str | None = None, verbose: bool = False) -> int:
    store = RunStore(working_dir)
    try:
        record = store.load(run_id) if run_id else store.latest()
    except RunStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(render(record, verbose=verbose))
    return 0
