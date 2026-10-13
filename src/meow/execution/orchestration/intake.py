"""Intake: turn an objective, Jira issues, a JQL query, or a tasks file into
a validated task list, running the decomposer where a task graph is missing."""

import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import fetch_issue_list, fetch_record
from meow.agents.orchestration import OrchestratorAgents
from meow.execution.orchestration.model import (
    MAX_ORCHESTRATOR_TASKS,
    OrchestratorRecord,
    OrchestratorTask,
    parse_tasks_document,
    validate_tasks,
)
from meow.execution.orchestration.store import OrchestratorStore
from meow.infrastructure.worktree import sanitize_name
from meow.integrations.issue_solver import _load_jira_config

_ATTEMPTS = 2
_ISSUE_FIELDS = ("key", "summary", "description")


@dataclass(frozen=True)
class IntakeRequest:
    """What the user asked the orchestrator to work on; exactly one source."""

    source: str
    objective: str | None = None
    jira_keys: tuple[str, ...] = ()
    jql: str | None = None
    tasks_file: Path | None = None

    def validate(self) -> None:
        """Raise ValueError unless exactly the source named by `source` is given."""
        given = [
            name
            for name, present in (
                ("objective", bool(self.objective and self.objective.strip())),
                ("jira", bool(self.jira_keys)),
                ("jql", bool(self.jql and self.jql.strip())),
                ("tasks", self.tasks_file is not None),
            )
            if present
        ]
        if given != [self.source]:
            raise ValueError(
                "Give exactly one input: an objective, --jira KEY ..., "
                "--jql QUERY, or --tasks FILE"
            )


def check_task_count(count: int) -> None:
    """Fail fast when more than `MAX_ORCHESTRATOR_TASKS` tasks are proposed."""
    if count > MAX_ORCHESTRATOR_TASKS:
        raise ValueError(
            f"{count} tasks is more than the {MAX_ORCHESTRATOR_TASKS} allowed; "
            "narrow the objective, JQL query, or task list"
        )


def _check_document_size(raw: object) -> None:
    entries = raw.get("tasks") if isinstance(raw, dict) else None
    if isinstance(entries, list):
        check_task_count(len(entries))


def load_tasks_file(path: Path) -> list[OrchestratorTask]:
    """Load and validate a `[[tasks]]` TOML file or a `{"tasks": [...]}` JSON file."""
    if path.suffix not in {".toml", ".json"}:
        raise ValueError(f"Unsupported tasks file {path.name}: use .toml or .json")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read tasks file {path}: {exc}") from exc
    try:
        raw = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except ValueError as exc:
        kind = path.suffix[1:].upper()
        raise ValueError(f"{path.name} is not valid {kind}: {exc}") from exc
    _check_document_size(raw)
    tasks = parse_tasks_document(raw)
    validate_tasks(tasks)
    return tasks


def dedupe_keys(keys: Sequence[str]) -> tuple[str, ...]:
    """Jira keys without blanks or repeats (case-insensitive), in the given order."""
    seen: dict[str, str] = {}
    for key in keys:
        cleaned = key.strip()
        if cleaned:
            seen.setdefault(cleaned.casefold(), cleaned)
    return tuple(seen.values())


def jira_task(issue: dict) -> OrchestratorTask:
    """The task for one fetched Jira issue (`key`, `summary`, `description`)."""
    key = issue["key"]
    return OrchestratorTask(
        id=sanitize_name(key),
        request=(
            f"Resolve Jira issue {key}: {issue['summary']}\n\n{issue['description']}"
        ),
        source_ref=key,
    )


async def fetch_jira_tasks(
    repo: Path, config: dict, request: IntakeRequest, agents: OrchestratorAgents
) -> list[OrchestratorTask]:
    """Fetch the requested Jira issues (read-only) as one task each."""
    jira_config = _load_jira_config(config)
    fetcher = agents.issue_fetcher(ProjectContext(repo, config), jira_config["mcp"])
    if request.source == "jql":
        issues = await fetch_issue_list(fetcher, request.jql or "")
    else:
        await fetcher.check_active()
        issues = []
        for key in dedupe_keys(request.jira_keys):
            issues.append(
                await fetch_record(
                    fetcher,
                    lambda output_file, key=key: fetcher.fetch(
                        key, jira_config["project_key"], output_file
                    ),
                    _ISSUE_FIELDS,
                    preflight=False,
                )
            )
    check_task_count(len(issues))
    if not issues:
        raise RuntimeError("No Jira issues matched")
    return [jira_task(issue) for issue in issues]


def _restore_drafts(
    drafts: Sequence[OrchestratorTask], decomposed: Sequence[OrchestratorTask]
) -> list[OrchestratorTask]:
    """Keep each fetched Jira task as is; take only its dependencies and paths."""
    by_ref = {draft.source_ref: draft for draft in drafts}
    if not by_ref:
        raise ValueError("The fetched Jira tasks were not recorded before decomposing")
    restored = []
    for task in decomposed:
        draft = by_ref.get(task.source_ref)
        if draft is None:
            raise ValueError(
                f"A Jira task set cannot gain tasks: {task.id!r} has no Jira key"
            )
        restored.append(
            replace(
                draft, depends_on=task.depends_on, expected_paths=task.expected_paths
            )
        )
    return restored


def save_decomposed(
    store: OrchestratorStore, orch_id: str, raw: object
) -> list[OrchestratorTask]:
    """Validate a decomposer's tasks document and journal it as the task list."""
    record = store.load(orch_id)
    keys = tuple(record.intake.get("jira_keys") or ())
    _check_document_size(raw)
    tasks = parse_tasks_document(raw, jira_keys=keys)
    if keys:
        tasks = _restore_drafts(record.tasks, tasks)
    validate_tasks(tasks)

    def mutate(current: OrchestratorRecord) -> None:
        current.tasks = tasks

    store.update(orch_id, mutate)
    return tasks


def drafts_text(drafts: Sequence[OrchestratorTask]) -> str:
    """The Jira drafts as the decomposer sees them (shared by CLI and native)."""
    return "\n\n".join(
        f"Task {draft.id} ({draft.source_ref}):\n{draft.request}" for draft in drafts
    )


def _read_document(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc


def _start_decomposition(
    store: OrchestratorStore, orch_id: str, drafts: list[OrchestratorTask] | None
) -> None:
    def mutate(record: OrchestratorRecord) -> None:
        if drafts:
            record.tasks = list(drafts)
            record.intake["jira_keys"] = [draft.source_ref for draft in drafts]

    store.update(orch_id, mutate)


def _count_attempt(store: OrchestratorStore, orch_id: str) -> None:
    def mutate(record: OrchestratorRecord) -> None:
        record.intake["attempts"] += 1

    store.update(orch_id, mutate)


async def decompose(  # ruff: ignore[too-many-arguments]
    repo: Path,
    config: dict,
    store: OrchestratorStore,
    orch_id: str,
    *,
    workdir: Path,
    drafts: list[OrchestratorTask] | None,
    agents: OrchestratorAgents,
) -> list[OrchestratorTask]:
    """Run the decomposer into `tasks.json`; one corrective retry, then fail.

    `drafts` (Jira) are recorded first and fixed: the decomposer may only add
    dependencies and expected paths. Raises ValueError after two invalid outputs.
    """
    _start_decomposition(store, orch_id, drafts)
    decomposer = agents.decomposer(ProjectContext(workdir, config))
    output_file = store.dir_for(orch_id) / "tasks.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    objective = store.load(orch_id).objective
    correction = None
    for _ in range(_ATTEMPTS):
        _count_attempt(store, orch_id)
        await decomposer.run(
            objective,
            output_file,
            task_drafts=drafts_text(drafts) if drafts else None,
            correction=correction,
        )
        try:
            return save_decomposed(store, orch_id, _read_document(output_file))
        except ValueError as exc:
            correction = str(exc)
    raise ValueError(
        f"The decomposer's output was invalid after {_ATTEMPTS} attempts: {correction}"
    )
