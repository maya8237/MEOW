"""Bounded scheduling for workers that own separate checkouts."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .model import TaskGraph, TaskSpec


@dataclass(frozen=True)
class TaskOutcome:
    id: str
    status: str
    worktree: str | None = None
    commit: str | None = None
    error: str | None = None


async def run_task_graph(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements, too-many-arguments]
    graph: TaskGraph,
    worker: Callable[[TaskSpec], Awaitable[TaskOutcome]],
    *,
    max_parallel: int,
    cancel: Callable[[], bool] = lambda: False,
    on_change: Callable[[str, str], None] | None = None,
    on_complete: Callable[[TaskOutcome], Awaitable[bool]] | None = None,
) -> tuple[TaskOutcome, ...]:
    """Run ready tasks; worker owns isolation and returns recoverable evidence."""
    graph.validate()
    if max_parallel < 1:
        raise ValueError("max_parallel must be positive")
    by_id = {spec.id: spec for spec in graph.tasks}
    completed: set[str] = set()
    started: set[str] = set()
    running: dict[asyncio.Task[TaskOutcome], str] = {}
    outcomes: list[TaskOutcome] = []
    stopped = False

    try:  # ruff: ignore[too-many-nested-blocks]
        while len(started) < len(graph.tasks) or running:
            if cancel():
                stopped = True
                for task in running:
                    task.cancel()
            if not stopped:
                for task_id in graph.ready(completed):
                    if len(running) >= max_parallel:
                        break
                    if task_id in started:
                        continue
                    started.add(task_id)
                    if on_change:
                        on_change(task_id, "running")
                    running[asyncio.create_task(worker(by_id[task_id]))] = task_id
            if not running:
                break
            finished, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            for task in finished:
                task_id = running.pop(task)
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    result = TaskOutcome(task_id, "cancelled")
                except Exception as exc:  # worker failure retains its checkout
                    result = TaskOutcome(task_id, "failed", error=str(exc))
                if result.status == "complete" and on_complete is not None:
                    try:
                        integrated = await on_complete(result)
                    except Exception as exc:
                        result = TaskOutcome(
                            task_id, "failed", result.worktree, result.commit, str(exc)
                        )
                    else:
                        if not integrated:
                            result = TaskOutcome(
                                task_id,
                                "failed",
                                result.worktree,
                                result.commit,
                                "integration failed",
                            )
                outcomes.append(result)
                if result.status == "complete":
                    completed.add(task_id)
                else:
                    stopped = True
                if on_change:
                    on_change(task_id, result.status)
    finally:
        if running:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
    return tuple(outcomes)
