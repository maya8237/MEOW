"""Pure readiness and launch ordering for orchestrator tasks."""

from collections.abc import Callable, Mapping, Sequence

from meow.execution.orchestration.model import OrchestratorRecord, OrchestratorTask

# Matches `meow.infrastructure.background.launch_background(repo, argv, *, seed)`.
Launcher = Callable[..., str]


def _reachable(start: str, children: Mapping[str, Sequence[str]]) -> int:
    """How many tasks can be reached from `start` through `children` links."""
    seen: set[str] = set()
    pending = list(children[start])
    while pending:
        current = pending.pop()
        if current not in seen and current != start:
            seen.add(current)
            pending.extend(children[current])
    return len(seen)


def transitive_dependents(tasks: Sequence[OrchestratorTask]) -> dict[str, int]:
    """For each task id, how many tasks depend on it directly or indirectly."""
    children: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            if dependency in children:
                children[dependency].append(task.id)
    return {task.id: _reachable(task.id, children) for task in tasks}


def ready_tasks(record: OrchestratorRecord) -> list[str]:
    """Pending tasks whose dependencies are all merged, most dependents first."""
    merged = {task.id for task in record.tasks if task.status == "merged"}
    dependents = transitive_dependents(record.tasks)
    ready = [
        (-dependents[task.id], position, task.id)
        for position, task in enumerate(record.tasks)
        if task.status == "pending" and set(task.depends_on) <= merged
    ]
    return [task_id for _, _, task_id in sorted(ready)]


def tasks_to_launch(record: OrchestratorRecord) -> list[str]:
    """Ready tasks to start now, up to `max_parallel` running children."""
    if record.phase != "running" or record.blocker is not None:
        return []
    running = sum(1 for task in record.tasks if task.status == "running")
    slots = max(record.max_parallel - running, 0)
    return ready_tasks(record)[:slots]
