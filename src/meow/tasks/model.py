"""Bounded, serializable task dependencies and file ownership."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from meow.infrastructure.worktree import _reject_reserved_name

MAX_TASKS = 16
# Task IDs become file, worktree, and branch names, so keep them to a safe set.
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (len(normalized) > 1 and normalized[1] == ":")
    ):
        raise ValueError(f"task ownership path must be project-relative: {value}")
    return "/".join(path.parts).casefold()


def owns(owner: str, path: str) -> bool:
    """Whether a normalized `owner` covers a normalized `path` ("" is the root)."""
    return not owner or path == owner or path.startswith(f"{owner}/")


def normalize_path(value: str) -> str:
    """Casefolded, slash-separated, project-relative form used to compare paths."""
    return "/".join(PurePosixPath(value.replace("\\", "/")).parts).casefold()


def _overlap(left: str, right: str) -> bool:
    return owns(left, right) or owns(right, left)


@dataclass(frozen=True)
class TaskSpec:
    id: str
    depends_on: tuple[str, ...]
    owned_paths: tuple[str, ...]
    verification: tuple[str, ...]


@dataclass(frozen=True)
class TaskGraph:
    tasks: tuple[TaskSpec, ...]

    def validate(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self,
    ) -> None:
        if not 1 <= len(self.tasks) <= MAX_TASKS:
            raise ValueError(f"task graph needs 1 to {MAX_TASKS} tasks")
        ids = [task.id for task in self.tasks]
        for task_id in ids:
            if not _TASK_ID.fullmatch(task_id):
                raise ValueError(
                    f"task ID {task_id!r} is invalid: use 1-64 letters, digits, "
                    "'-' or '_', starting with a letter or digit (e.g. task-1)"
                )
        # Task IDs become file and worktree names; compare them the way a
        # case-insensitive filesystem would, and refuse Windows device names.
        if len({task_id.casefold() for task_id in ids}) != len(ids):
            raise ValueError("duplicate task ID")
        for task_id in ids:
            _reject_reserved_name(task_id)
        by_id = {task.id: task for task in self.tasks}
        for task in self.tasks:
            if not task.owned_paths:
                raise ValueError(f"task {task.id} needs owned paths")
            if any(dep not in by_id for dep in task.depends_on):
                raise ValueError(f"task {task.id} has unknown dependency")
            if len(set(task.depends_on)) != len(task.depends_on):
                raise ValueError(f"task {task.id} has duplicate dependencies")
            for path in task.owned_paths:
                _path(path)

        visiting: set[str] = set()
        visited: set[str] = set()

        def ancestors(task_id: str) -> set[str]:
            if task_id in visiting:
                raise ValueError("task dependency cycle")
            if task_id in visited:
                return cache[task_id]
            visiting.add(task_id)
            result = set(by_id[task_id].depends_on)
            for dependency in by_id[task_id].depends_on:
                result.update(ancestors(dependency))
            visiting.remove(task_id)
            visited.add(task_id)
            cache[task_id] = result
            return result

        cache: dict[str, set[str]] = {}
        for task_id in ids:
            ancestors(task_id)
        for position, left in enumerate(self.tasks):
            for right in self.tasks[position + 1 :]:
                if left.id in cache[right.id] or right.id in cache[left.id]:
                    continue
                if any(
                    _overlap(_path(a), _path(b))
                    for a in left.owned_paths
                    for b in right.owned_paths
                ):
                    raise ValueError(
                        f"independent tasks {left.id} and {right.id} overlap"
                    )

    def ready(self, completed: set[str]) -> tuple[str, ...]:
        self.validate()
        return tuple(
            task.id
            for task in self.tasks
            if task.id not in completed and set(task.depends_on) <= completed
        )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"tasks": [asdict(task) for task in self.tasks]}


_LIST_FIELDS = ("depends_on", "owned_paths", "verification")


def _string_list(value: object) -> tuple[str, ...]:
    """Normalize a planner-written list field.

    Planners often write a single command or a prose paragraph instead of a
    list; accept a string (split on lines, bullets dropped) or null so a
    near-valid graph is repaired instead of discarded.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        lines = (line.strip().lstrip("-*").strip() for line in value.splitlines())
        return tuple(line for line in lines if line)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError("task entry fields have invalid types")


def load_task_graph(  # ruff: ignore[complex-structure]
    plan_file: Path,
) -> TaskGraph | None:
    """Load an optional planner artifact; reject malformed graphs before work."""
    path = Path(f"{plan_file}.tasks.json")
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid task graph at {path}") from exc
    if not isinstance(raw, dict) or set(raw) != {"tasks"}:
        raise ValueError("task graph must contain only tasks")
    if not isinstance(raw["tasks"], list):
        raise ValueError("task graph tasks must be a list")
    tasks = []
    for entry in raw["tasks"]:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "depends_on",
            "owned_paths",
            "verification",
        }:
            raise ValueError("task entry has missing or unknown fields")
        if not isinstance(entry["id"], str):
            raise ValueError("task entry fields have invalid types")
        lists = {key: _string_list(entry[key]) for key in _LIST_FIELDS}
        tasks.append(
            TaskSpec(
                entry["id"],
                lists["depends_on"],
                lists["owned_paths"],
                lists["verification"],
            )
        )
    graph = TaskGraph(tuple(tasks))
    graph.validate()
    return graph
