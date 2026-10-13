"""Orchestrator records, task validation, and naming."""

import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field

from meow.infrastructure.worktree import sanitize_name
from meow.tasks.model import check_task_ids, dependency_ancestors, project_path

MAX_ORCHESTRATOR_TASKS = 32
PHASES = (
    "decomposing",
    "running",
    "reviewing",
    "delivering",
    "delivered",
    "blocked",
    "failed",
    "cancelled",
)
TERMINAL_PHASES = frozenset({"delivered", "failed", "cancelled"})
TASK_STATUSES = ("pending", "running", "passed", "failed", "merged", "blocked")
SOURCES = ("objective", "jira", "jql", "tasks")
MODES = ("cli", "native")
SCHEMA_VERSION = 1
TASK_ENTRY_KEYS = frozenset({
    "id",
    "request",
    "depends_on",
    "expected_paths",
    "source_ref",
})
_ORCHESTRATOR_ID = re.compile(r"[0-9a-f]{12}\Z")


@dataclass
class OrchestratorTask:
    id: str
    request: str
    source_ref: str | None = None
    depends_on: list[str] = field(default_factory=list)
    expected_paths: list[str] = field(default_factory=list)
    status: str = "pending"
    branch: str | None = None
    worktree: str | None = None
    run_ids: list[str] = field(default_factory=list)
    cycles: int = 0
    history: list[dict] = field(default_factory=list)
    next_attempt: dict | None = None


def _default_options() -> dict:
    return {"test": False, "pr": True}


def _default_integration() -> dict:
    return {
        "branch": None,
        "worktree": None,
        "merged": [],
        "pre_merge_commit": None,
        "merging": None,
    }


def _default_review() -> dict:
    return {"round": 0, "verdict": None, "review_file": None}


def _default_delivery() -> dict:
    return {"pushed_commit": None, "pr": None, "pr_error": None}


def _default_intake() -> dict:
    return {"attempts": 0, "jira_keys": [], "dry_run": False}


@dataclass
class OrchestratorRecord:
    id: str
    objective: str
    source: str
    base_branch: str
    mode: str
    phase: str = "decomposing"
    created_at: str = ""
    updated_at: str = ""
    max_parallel: int = 3
    options: dict = field(default_factory=_default_options)
    integration: dict = field(default_factory=_default_integration)
    review: dict = field(default_factory=_default_review)
    delivery: dict = field(default_factory=_default_delivery)
    blocker: dict | None = None
    error: dict | None = None
    intake: dict = field(default_factory=_default_intake)
    tasks: list[OrchestratorTask] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def task(self, task_id: str) -> OrchestratorTask:
        for item in self.tasks:
            if item.id == task_id:
                return item
        raise KeyError(task_id)


def validate_tasks(tasks: Sequence[OrchestratorTask]) -> None:
    """Raise ValueError when `tasks` is not a valid orchestrator task graph."""
    if not 1 <= len(tasks) <= MAX_ORCHESTRATOR_TASKS:
        raise ValueError(
            f"an orchestrator needs 1 to {MAX_ORCHESTRATOR_TASKS} tasks, got "
            f"{len(tasks)}; narrow the objective or JQL"
        )
    try:
        check_task_ids([task.id for task in tasks])
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    for task in tasks:
        if not task.request.strip():
            raise ValueError(f"task {task.id} needs a request")
        for path in task.expected_paths:
            project_path(path)
    dependency_ancestors({task.id: task.depends_on for task in tasks})


def _text_list(entry: dict, key: str, task_id: str) -> list[str]:
    value = entry.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"task {task_id}: {key!r} must be a list of strings")
    return list(value)


def _task_from_entry(position: int, entry: object) -> OrchestratorTask:
    if not isinstance(entry, dict):
        raise ValueError(f"task entry {position} must be an object")
    unknown = sorted(set(entry) - TASK_ENTRY_KEYS)
    if unknown:
        raise ValueError(
            f"task entry {position} has unknown keys: {', '.join(unknown)}"
        )
    task_id = entry.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError(f"task entry {position} needs a string 'id'")
    request = entry.get("request")
    if not isinstance(request, str) or not request.strip():
        raise ValueError(f"task {task_id} needs a non-empty 'request'")
    source_ref = entry.get("source_ref")
    if source_ref is not None and not isinstance(source_ref, str):
        raise ValueError(f"task {task_id}: 'source_ref' must be a string or null")
    return OrchestratorTask(
        task_id,
        request,
        source_ref,
        _text_list(entry, "depends_on", task_id),
        _text_list(entry, "expected_paths", task_id),
    )


def _check_jira_refs(refs: list[str | None], jira_keys: Sequence[str]) -> None:
    if len(set(refs)) != len(refs):
        raise ValueError("a Jira key appears in more than one task")
    missing = [key for key in jira_keys if key not in refs]
    if missing:
        raise ValueError(f"Jira keys missing from tasks: {', '.join(missing)}")
    extra = [ref for ref in refs if ref not in jira_keys]
    if extra:
        raise ValueError(f"tasks reference unrequested Jira keys: {', '.join(extra)}")


def _check_jira_keys(tasks: list[OrchestratorTask], jira_keys: Sequence[str]) -> None:
    for task in tasks:
        if task.source_ref is None:
            raise ValueError(
                f"task {task.id} has no source_ref; every task must map to one "
                "requested Jira key"
            )
    _check_jira_refs([task.source_ref for task in tasks], jira_keys)
    for task in tasks:
        expected = sanitize_name(task.source_ref)
        if task.id != expected:
            raise ValueError(
                f"task for Jira key {task.source_ref} must have id {expected!r}, "
                f"got {task.id!r}"
            )


def parse_tasks_document(
    raw: object, *, jira_keys: Sequence[str] = ()
) -> list[OrchestratorTask]:
    """Turn a `{"tasks": [...]}` document into validated tasks."""
    if not isinstance(raw, dict) or set(raw) != {"tasks"}:
        raise ValueError("tasks document must be an object with only a 'tasks' list")
    entries = raw["tasks"]
    if not isinstance(entries, list):
        raise ValueError("tasks document must be an object with only a 'tasks' list")
    tasks = [
        _task_from_entry(position, item) for position, item in enumerate(entries, 1)
    ]
    if jira_keys:
        _check_jira_keys(tasks, jira_keys)
    validate_tasks(tasks)
    return tasks


def new_orchestrator_id() -> str:
    return secrets.token_hex(6)


def is_orchestrator_id(value: str) -> bool:
    return bool(_ORCHESTRATOR_ID.fullmatch(value))


def integration_branch(prefix: str, orch_id: str) -> str:
    return f"{prefix}{orch_id}"


def task_branch(prefix: str, orch_id: str, task_id: str) -> str:
    return f"{prefix}{orch_id}-{task_id}"


def integration_worktree_name(orch_id: str) -> str:
    return f"orch-{orch_id}"


def task_worktree_name(orch_id: str, task_id: str) -> str:
    return f"orch-{orch_id}-{task_id}"
