"""Triage of failed orchestrator tasks: parse decisions and apply them to the DAG."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from meow.execution.delivery import _git
from meow.execution.orchestration.integration import _detail, _git_committing
from meow.execution.orchestration.model import (
    OrchestratorRecord,
    OrchestratorTask,
    validate_tasks,
)
from meow.execution.run_state import RunStateError, RunStore

TRIAGE_ACTIONS = ("retry", "resume", "rescope", "reintegrate", "blocked")
FAILURE_CAUSES = ("child-failed", "worker-died", "launch-failed", "integration")
BLOCKER_CAUSES = (
    "recovery-exhausted",
    "needs-human",
    "repeated-decision",
    "invalid-decision",
    "reintegrate-conflict",
    "objective-review",
)
_DECISION_KEYS = frozenset({
    "action",
    "rationale",
    "notes",
    "request",
    "subtasks",
    "prerequisite",
    "needed",
})
_DRAFT_KEYS = frozenset({"id", "request", "depends_on", "expected_paths"})


@dataclass(frozen=True)
class TriageDecision:
    action: str
    rationale: str
    notes: str = ""
    request: str | None = None
    subtasks: tuple[dict, ...] = ()
    prerequisite: dict | None = None
    needed: str = ""

    def key(self) -> str:
        """Canonical text used to tell whether two decisions are identical."""
        return json.dumps(
            {
                "action": self.action,
                "notes": self.notes,
                "request": self.request,
                "subtasks": list(self.subtasks),
                "prerequisite": self.prerequisite,
            },
            sort_keys=True,
        )


class _DecisionError(ValueError):
    """A decision that cannot be applied; the task is blocked with `cause`."""

    def __init__(self, message: str, cause: str = "invalid-decision"):
        super().__init__(message)
        self.cause = cause


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(raw: dict, key: str, *, required: bool = False, where: str = "") -> str:
    """A string field ("" when absent); `required` rejects blank text."""
    value = raw.get(key)
    label = f"{where}{key}"
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if required and not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _strings(raw: dict, key: str, where: str) -> list[str]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{where}{key} must be a list of non-empty strings")
    return list(value)


def _draft(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    unknown = sorted(set(value) - _DRAFT_KEYS)
    if unknown:
        raise ValueError(f"{where} has unknown key(s): {', '.join(unknown)}")
    prefix = f"{where} "
    return {
        "id": _text(value, "id", required=True, where=prefix),
        "request": _text(value, "request", required=True, where=prefix),
        "depends_on": _strings(value, "depends_on", prefix),
        "expected_paths": _strings(value, "expected_paths", prefix),
    }


def _subtask_drafts(value: object) -> tuple[dict, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise ValueError("subtasks must be a non-empty list")
    return tuple(_draft(item, "subtask") for item in value)


def _check_rescope_shape(decision: TriageDecision) -> None:
    given = [
        name
        for name, value in (
            ("request", decision.request),
            ("subtasks", decision.subtasks),
            ("prerequisite", decision.prerequisite),
        )
        if value
    ]
    if decision.action == "rescope":
        if len(given) != 1:
            raise ValueError(
                "rescope needs exactly one of request, subtasks, prerequisite"
            )
    elif given:
        raise ValueError(
            f"{decision.action} takes no request, subtasks, or prerequisite"
        )


def parse_decision(raw: object) -> TriageDecision:
    """Validate a decision document; ValueError names the first problem."""
    if not isinstance(raw, dict):
        raise ValueError("Triage decision must be a JSON object")
    unknown = sorted(set(raw) - _DECISION_KEYS)
    if unknown:
        raise ValueError(f"Unknown decision key(s): {', '.join(unknown)}")
    action = raw.get("action")
    if action not in TRIAGE_ACTIONS:
        raise ValueError(
            f"Unknown action {action!r}; expected one of: {', '.join(TRIAGE_ACTIONS)}"
        )
    prerequisite = raw.get("prerequisite")
    decision = TriageDecision(
        action=action,
        rationale=_text(raw, "rationale", required=True),
        notes=_text(raw, "notes"),
        request=_text(raw, "request") or None,
        subtasks=_subtask_drafts(raw.get("subtasks")),
        prerequisite=(
            None if prerequisite is None else _draft(prerequisite, "prerequisite")
        ),
        needed=_text(raw, "needed"),
    )
    _check_rescope_shape(decision)
    return decision


def decision_file(directory: Path, task: OrchestratorTask) -> Path:
    """Where the triage role writes its decision for the task's next cycle."""
    return directory / f"decision-{task.id}-{task.cycles + 1}.json"


def append_triage_notes(plan_file: Path, attempt: int, notes: str) -> None:
    """Append triage guidance to the plan so the next attempt reads it."""
    with plan_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n## Orchestrator triage notes (attempt {attempt})\n\n")
        handle.write(notes.strip() + "\n")


def _last_failure(task: OrchestratorTask) -> dict | None:
    return next(
        (entry for entry in reversed(task.history) if entry.get("action") == "failed"),
        None,
    )


def _block(
    record: OrchestratorRecord,
    task: OrchestratorTask,
    cause: str,
    needed: str,
) -> None:
    """Block the task and the orchestrator; the first blocker is the one kept."""
    failure = _last_failure(task)
    evidence = list(failure["evidence"]) if failure else []
    task.status = "blocked"
    task.next_attempt = None
    task.history.append({
        "at": _now(),
        "action": "blocked",
        "cause": cause,
        "rationale": needed,
        "evidence": evidence,
    })
    if record.blocker is None:
        record.blocker = {
            "task_id": task.id,
            "cause": cause,
            "evidence": evidence,
            "needed": needed,
        }
    record.phase = "blocked"


def record_failure(  # ruff: ignore[too-many-arguments]
    record: OrchestratorRecord,
    task_id: str,
    *,
    cause: str,
    evidence: list[str],
    max_cycles: int,
) -> Literal["triage", "blocked", "recorded"]:
    """Record a failed attempt; say whether triage should run."""
    if cause not in FAILURE_CAUSES:
        raise ValueError(f"Unknown failure cause {cause!r}")
    task = record.task(task_id)
    task.history.append({
        "at": _now(),
        "action": "failed",
        "cause": cause,
        "rationale": "",
        "evidence": list(evidence),
    })
    task.status = "failed"
    if record.phase == "blocked":
        return "recorded"
    if task.cycles >= max_cycles:
        _block(
            record,
            task,
            "recovery-exhausted",
            f"Task {task.id} used all {max_cycles} recovery cycles. "
            "Fix the cause by hand or change the task, then resume.",
        )
        return "blocked"
    return "triage"


def _last_plan_file(repo: Path, task: OrchestratorTask) -> Path | None:
    """The previous attempt's plan file, when it exists on disk."""
    if not task.run_ids:
        return None
    try:
        recorded = RunStore(repo).load(task.run_ids[-1]).plan_file
    except (RunStateError, OSError):
        return None
    plan = Path(recorded) if recorded else None
    return plan if plan is not None and plan.is_file() else None


def _record_decision(task: OrchestratorTask, decision: TriageDecision) -> None:
    failure = _last_failure(task)
    task.history.append({
        "at": _now(),
        "action": decision.action,
        "cause": failure["cause"] if failure else None,
        "rationale": decision.rationale,
        "evidence": [],
        "decision_key": decision.key(),
        "request": task.request,
    })


def _is_repeat(task: OrchestratorTask, decision: TriageDecision) -> bool:
    """Same decision as the previous one, with the request unchanged since."""
    for entry in reversed(task.history):
        if "decision_key" in entry:
            return (
                entry["decision_key"] == decision.key()
                and entry["request"] == task.request
            )
    return False


def _requeue(
    task: OrchestratorTask, decision: TriageDecision, plan: Path | None
) -> list[str]:
    task.cycles += 1
    task.status = "pending"
    task.next_attempt = {
        "resume_at": "review" if plan else "generate",
        "plan_file": str(plan) if plan else None,
    }
    _record_decision(task, decision)
    return [task.id]


def _write_notes(plan: Path, task: OrchestratorTask, decision: TriageDecision) -> None:
    try:
        append_triage_notes(plan, task.cycles + 1, decision.notes or decision.rationale)
    except OSError as error:
        raise _DecisionError(f"Cannot write triage notes to {plan}: {error}") from error


def _require_plan(repo: Path, task: OrchestratorTask, action: str) -> Path:
    plan = _last_plan_file(repo, task)
    if plan is None:
        raise _DecisionError(
            f"{action} needs the previous attempt's plan file, which is missing; "
            "use retry or rescope"
        )
    return plan


def _retry(
    repo: Path,
    record: OrchestratorRecord,
    task: OrchestratorTask,
    decision: TriageDecision,
) -> list[str]:
    return _requeue(task, decision, _last_plan_file(repo, task))


def _resume(
    repo: Path,
    record: OrchestratorRecord,
    task: OrchestratorTask,
    decision: TriageDecision,
) -> list[str]:
    plan = _require_plan(repo, task, "resume")
    _write_notes(plan, task, decision)
    return _requeue(task, decision, plan)


def _merge_integration_tip(record: OrchestratorRecord, task: OrchestratorTask) -> None:
    branch = record.integration.get("branch")
    if not branch or not task.worktree:
        raise _DecisionError(
            "reintegrate needs the integration branch and task worktree"
        )
    worktree = Path(task.worktree)
    merged = _git_committing(worktree, "merge", "--no-edit", branch)
    if merged.returncode:
        _git(worktree, "merge", "--abort", check=False)
        raise _DecisionError(
            f"Merging {branch} into {task.branch} failed: {_detail(merged)}",
            "reintegrate-conflict",
        )


def _reintegrate(
    repo: Path,
    record: OrchestratorRecord,
    task: OrchestratorTask,
    decision: TriageDecision,
) -> list[str]:
    plan = _require_plan(repo, task, "reintegrate")
    _merge_integration_tip(record, task)
    _write_notes(plan, task, decision)
    return _requeue(task, decision, plan)


def _task_from_draft(
    draft: dict, depends_on: list[str], cycles: int, entry: dict
) -> OrchestratorTask:
    return OrchestratorTask(
        id=draft["id"],
        request=draft["request"],
        depends_on=depends_on,
        expected_paths=list(draft["expected_paths"]),
        cycles=cycles,
        history=[entry],
    )


def _validated(tasks: list[OrchestratorTask]) -> None:
    try:
        validate_tasks(tasks)
    except ValueError as error:
        raise _DecisionError(f"Rescope rejected: {error}") from error


def _split(
    record: OrchestratorRecord, task: OrchestratorTask, decision: TriageDecision
) -> list[str]:
    cycles = task.cycles + 1
    entry = {
        "at": _now(),
        "action": "split",
        "cause": None,
        "rationale": decision.rationale,
        "evidence": [],
        "parent": task.id,
    }
    parts = [
        _task_from_draft(
            draft,
            list(dict.fromkeys([*task.depends_on, *draft["depends_on"]])),
            cycles,
            dict(entry),
        )
        for draft in decision.subtasks
    ]
    for part in parts:
        part.source_ref = task.source_ref
    ids = [part.id for part in parts]
    rewired = {
        other.id: [
            new
            for dep in other.depends_on
            for new in (ids if dep == task.id else [dep])
        ]
        for other in record.tasks
        if task.id in other.depends_on
    }
    candidate = [
        part
        for other in record.tasks
        for part in (
            parts
            if other is task
            else [replace(other, depends_on=rewired.get(other.id, other.depends_on))]
        )
    ]
    _validated(candidate)
    for other in record.tasks:
        if other.id in rewired:
            other.depends_on = rewired[other.id]
    position = record.tasks.index(task)
    record.tasks[position : position + 1] = parts
    return ids


def _add_prerequisite(
    record: OrchestratorRecord, task: OrchestratorTask, decision: TriageDecision
) -> list[str]:
    entry = {
        "at": _now(),
        "action": "prerequisite",
        "cause": None,
        "rationale": decision.rationale,
        "evidence": [],
        "for": task.id,
    }
    draft = decision.prerequisite
    prerequisite = _task_from_draft(draft, list(draft["depends_on"]), 0, entry)
    depends_on = [*task.depends_on, prerequisite.id]
    candidate = [
        part
        for other in record.tasks
        for part in (
            [prerequisite, replace(other, depends_on=depends_on)]
            if other is task
            else [other]
        )
    ]
    _validated(candidate)
    task.depends_on = depends_on
    record.tasks.insert(record.tasks.index(task), prerequisite)
    task.cycles += 1
    task.status = "pending"
    task.next_attempt = {
        "resume_at": "generate",
        "plan_file": None,
        "reintegrate": True,
    }
    _record_decision(task, decision)
    return [prerequisite.id, task.id]


def _rescope(
    repo: Path,
    record: OrchestratorRecord,
    task: OrchestratorTask,
    decision: TriageDecision,
) -> list[str]:
    if decision.subtasks:
        return _split(record, task, decision)
    if decision.prerequisite:
        return _add_prerequisite(record, task, decision)
    task.request = decision.request
    return _requeue(task, decision, None)


_EFFECTS = {
    "retry": _retry,
    "resume": _resume,
    "reintegrate": _reintegrate,
    "rescope": _rescope,
}


def _refusal(
    task: OrchestratorTask, decision: TriageDecision, max_cycles: int
) -> tuple[str, str] | None:
    """The blocker cause and text when the decision must not be applied."""
    if decision.action == "blocked":
        return "needs-human", decision.needed or decision.rationale
    if task.cycles >= max_cycles:
        return (
            "recovery-exhausted",
            f"Task {task.id} used all {max_cycles} recovery cycles.",
        )
    if _is_repeat(task, decision):
        return (
            "repeated-decision",
            f"Triage chose {decision.action} again with nothing changed.",
        )
    return None


def apply_decision(  # ruff: ignore[too-many-arguments]
    repo: Path,
    record: OrchestratorRecord,
    task_id: str,
    decision: TriageDecision,
    *,
    max_cycles: int,
) -> list[str]:
    """Apply a decision to a `failed` task; return the ids now `pending`.

    The record is changed in place and not saved. A decision that cannot be
    applied blocks the task and the orchestrator and returns [].
    """
    task = record.task(task_id)
    if task.status != "failed":
        raise ValueError(f"Task {task_id} is {task.status}, not failed")
    refusal = _refusal(task, decision, max_cycles)
    if refusal is None:
        try:
            return _EFFECTS[decision.action](repo, record, task, decision)
        except _DecisionError as error:
            refusal = (error.cause, str(error))
    _block(record, task, *refusal)
    return []


def _reject(record: OrchestratorRecord, task_id: str, message: str) -> list[str]:
    _block(record, record.task(task_id), "invalid-decision", message)
    return []


def apply_decision_document(  # ruff: ignore[too-many-arguments]
    repo: Path,
    record: OrchestratorRecord,
    task_id: str,
    raw: object,
    *,
    max_cycles: int,
) -> list[str]:
    """Parse and apply a decision document; invalid ones block the task."""
    try:
        decision = parse_decision(raw)
    except ValueError as error:
        return _reject(record, task_id, str(error))
    return apply_decision(repo, record, task_id, decision, max_cycles=max_cycles)


def apply_decision_file(  # ruff: ignore[too-many-arguments]
    repo: Path,
    record: OrchestratorRecord,
    task_id: str,
    path: Path,
    *,
    max_cycles: int,
) -> list[str]:
    """Read the decision file the triage role wrote and apply it."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        message = f"Triage decision file {path} is missing or unreadable"
        return _reject(record, task_id, message)
    except ValueError as error:
        message = f"Triage decision file {path} is not valid JSON: {error}"
        return _reject(record, task_id, message)
    return apply_decision_document(repo, record, task_id, raw, max_cycles=max_cycles)
