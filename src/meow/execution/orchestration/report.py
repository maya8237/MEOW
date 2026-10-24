"""Everything an orchestrator tells a person: the report (also the pull request
body), `status` output, the one-line rollup, and the process exit code."""

import os
from pathlib import Path

from meow.execution.orchestration.model import OrchestratorRecord, OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import RunStateError, RunStore
from meow.infrastructure.usage import usage_totals

EXIT_DELIVERED_WITHOUT_PR = 3
EXIT_BLOCKED = 2
EXIT_CANCELLED = 130
EXIT_FAILED = 1


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def task_label(task: OrchestratorTask) -> str:
    """`id`, or `id (KEY)` when the Jira key differs from the id."""
    if task.source_ref and task.source_ref != task.id:
        return f"{task.id} ({task.source_ref})"
    return task.id


def merged_count(record: OrchestratorRecord) -> int:
    return sum(1 for task in record.tasks if task.status == "merged")


def rollup_line(record: OrchestratorRecord) -> str:
    return (
        f"orchestrator {record.id} {record.phase} "
        f"{merged_count(record)}/{len(record.tasks)} merged"
    )


def has_pull_request(record: OrchestratorRecord) -> bool:
    pr = record.delivery.get("pr")
    return isinstance(pr, dict) and bool(pr.get("url"))


def exit_code(record: OrchestratorRecord) -> int:
    """0 delivered with a PR (or `--no-mr`), 3 delivered without one, 2 blocked,
    130 cancelled, 1 anything else."""
    if record.phase == "delivered":
        if has_pull_request(record) or not record.options.get("pr", True):
            return 0
        return EXIT_DELIVERED_WITHOUT_PR
    return {"blocked": EXIT_BLOCKED, "cancelled": EXIT_CANCELLED}.get(
        record.phase, EXIT_FAILED
    )


def pull_request_lines(record: OrchestratorRecord) -> list[str]:
    delivery = record.delivery
    if has_pull_request(record):
        pr = delivery["pr"]
        return [f"Pull request: {pr['url']} ({pr.get('provider', 'unknown')})"]
    if delivery.get("pr_error"):
        lines = [f"MR not opened: {delivery['pr_error']}"]
        if delivery.get("pr_manual_link"):
            lines.append(f"Open it manually: {delivery['pr_manual_link']}")
        return lines
    if not record.options.get("pr", True):
        return ["Pull request: not requested (--no-mr)"]
    return ["Pull request: not opened yet"]


def resume_lines(orch_id: str) -> list[str]:
    return [
        "Resume with either:",
        f"    meow resume {orch_id} --continue",
    ]


def blocker_lines(record: OrchestratorRecord) -> list[str]:
    blocker = record.blocker
    if not blocker:
        return []
    lines = [
        f"Blocked task: {blocker.get('task_id') or '(the whole objective)'}",
        f"Cause: {blocker['cause']}",
        f"Needed: {blocker['needed']}",
    ]
    lines.extend(f"Evidence: {path}" for path in blocker.get("evidence", ()))
    return [*lines, *resume_lines(record.id)]


def _add(left: float | None, right: float | None) -> float | None:
    if right is None:
        return left
    return right if left is None else left + right


def run_usage(repo: Path, run_ids: list[str]) -> tuple[float | None, float | None]:
    """(tokens, cost) summed over child runs; None when none reported a value."""
    store = RunStore(repo)
    tokens = cost = None
    for run_id in run_ids:
        try:
            totals = usage_totals(store.load(run_id).usage)
        except RunStateError:
            continue
        tokens = _add(tokens, totals["tokens"])
        cost = _add(cost, totals["cost_usd"])
    return tokens, cost


def usage_text(tokens: float | None, cost: float | None) -> str:
    if tokens is None and cost is None:
        return "unavailable"
    shown_tokens = "unavailable" if tokens is None else f"{tokens:g}"
    shown_cost = "unavailable" if cost is None else f"${cost:.2f}"
    return f"tokens {shown_tokens}, cost {shown_cost}"


def history_line(entry: dict) -> str:
    line = str(entry.get("action", "?"))
    if entry.get("cause"):
        line += f" ({entry['cause']})"
    if entry.get("rationale"):
        line += f": {entry['rationale']}"
    return line


def task_lines(repo: Path, task: OrchestratorTask) -> list[str]:
    lines = [
        f"### {task_label(task)}",
        "",
        f"- Status: {task.status}",
        f"- Cycles: {task.cycles}",
        f"- Branch: {task.branch or '-'}",
        f"- Runs: {', '.join(task.run_ids) or '-'}",
        f"- Usage: {usage_text(*run_usage(repo, task.run_ids))}",
    ]
    if task.history:
        lines.append("- History:")
        lines.extend(f"  - {history_line(entry)}" for entry in task.history)
    return lines


def resolved_conflict_lines(record: OrchestratorRecord) -> list[str]:
    found = [
        f"- {task.id}: {entry['rationale']}"
        for task in record.tasks
        for entry in task.history
        if entry.get("action") == "conflict-resolved"
    ]
    return found or ["None."]


def review_lines(record: OrchestratorRecord) -> list[str]:
    review = record.review
    if not review.get("verdict"):
        return ["- Verdict: not run"]
    lines = [f"- Verdict: {review['verdict']} (round {review['round']})"]
    if review.get("review_file"):
        lines.append(f"- Review file: {review['review_file']}")
    return lines


def outcome_lines(record: OrchestratorRecord) -> list[str]:
    lines = [
        f"- Phase: {record.phase}",
        f"- Source: {record.source}",
        f"- Base branch: {record.base_branch}",
        f"- MR target: {record.options.get('target_branch') or record.base_branch}",
        f"- Integration branch: {record.integration.get('branch') or '-'}",
    ]
    if record.delivery.get("pushed_commit"):
        lines.append(f"- Pushed commit: {record.delivery['pushed_commit']}")
    lines.extend(f"- {line}" for line in pull_request_lines(record))
    if record.error:
        lines.append(f"- Error in {record.error['phase']}: {record.error['message']}")
    return lines


def render_report(repo: Path, record: OrchestratorRecord) -> str:
    """The full report; for a blocked orchestrator the blocker leads."""
    runs = [run_id for task in record.tasks for run_id in task.run_ids]
    lines = [f"# Orchestrator {record.id}: {record.phase}", ""]
    if record.phase == "blocked":
        lines += ["## Blocker", "", *blocker_lines(record), ""]
    lines += ["## Objective", "", record.objective.strip(), ""]
    lines += ["## Outcome", "", *outcome_lines(record), ""]
    lines += ["## Tasks", ""]
    for task in record.tasks:
        lines += [*task_lines(repo, task), ""]
    lines += ["## Conflicts resolved", "", *resolved_conflict_lines(record), ""]
    lines += ["## Final review", "", *review_lines(record), ""]
    lines += ["## Usage", "", f"- Total: {usage_text(*run_usage(repo, runs))}", ""]
    return "\n".join(lines)


def write_report(repo: Path, record: OrchestratorRecord) -> Path:
    path = OrchestratorStore(repo).dir_for(record.id) / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("report.md.tmp")
    temporary.write_text(render_report(repo, record), encoding="utf-8")
    os.replace(temporary, path)
    return path


def render_status(record: OrchestratorRecord) -> str:
    """The `meow status` output."""
    lines = [
        f"Orchestrator {record.id}",
        f"Phase: {record.phase}",
        f"Mode: {record.mode}",
        f"Objective: {first_line(record.objective)}",
        f"Base: {record.base_branch} -> {record.integration.get('branch') or '-'}",
        f"MR target: {record.options.get('target_branch') or record.base_branch}",
        f"Tasks: {merged_count(record)}/{len(record.tasks)} merged",
    ]
    for task in record.tasks:
        run = task.run_ids[-1] if task.run_ids else "-"
        lines.append(
            f"  {task.id}: {task.status}, cycles {task.cycles}, "
            f"branch {task.branch or '-'}, run {run}"
        )
    if record.options.get("force_disable_tests"):
        lines.append("Tests: SKIPPED (--force-disable-tests); lint/build retained")
    lines.extend(blocker_lines(record))
    if record.error:
        lines.append(f"Error in {record.error['phase']}: {record.error['message']}")
    if record.phase in {"delivering", "delivered"}:
        lines.extend(pull_request_lines(record))
    return "\n".join(lines)
