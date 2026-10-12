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


class PermanentTaskError(RuntimeError):
    """A deterministic task failure; retrying the worker cannot change it."""


# Deterministic failures: bad ownership, empty diffs, failed verification.
_PERMANENT = (ValueError, PermanentTaskError)


async def _attempt(
    worker: Callable[[TaskSpec], Awaitable[TaskOutcome]],
    spec: TaskSpec,
    retries: int,
) -> TaskOutcome:
    """Run the worker, retrying transient exceptions (crashes, I/O, timeouts)."""
    attempt = 0
    while True:
        try:
            return await worker(spec)
        except asyncio.CancelledError:
            raise
        except _PERMANENT:
            raise
        except Exception:
            attempt += 1
            if attempt > retries:
                raise
            await asyncio.sleep(min(2.0 * attempt, 10.0))


async def run_task_graph(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements, too-many-arguments]
    graph: TaskGraph,
    worker: Callable[[TaskSpec], Awaitable[TaskOutcome]],
    *,
    max_parallel: int,
    cancel: Callable[[], bool] = lambda: False,
    on_change: Callable[[str, str], None] | None = None,
    on_complete: Callable[[TaskOutcome], Awaitable[bool]] | None = None,
    on_outcome: Callable[[TaskOutcome], None] | None = None,
    completed: frozenset[str] | set[str] = frozenset(),
    retries: int = 1,
) -> tuple[TaskOutcome, ...]:
    """Run ready tasks; worker owns isolation and returns recoverable evidence.

    A failed task only blocks the tasks that depend on it: independent work
    keeps running, and transient worker exceptions are retried. Cancellation
    and integration failures (which share one checkout) stop new work.
    `completed` names tasks already integrated by an earlier attempt.
    """
    graph.validate()
    if max_parallel < 1:
        raise ValueError("max_parallel must be positive")
    by_id = {spec.id: spec for spec in graph.tasks}
    done: set[str] = {task_id for task_id in completed if task_id in by_id}
    failed: dict[str, str] = {}
    started: set[str] = set(done)
    running: dict[asyncio.Task[TaskOutcome], str] = {}
    outcomes: list[TaskOutcome] = [
        TaskOutcome(spec.id, "complete")
        for spec in graph.tasks
        if spec.id in done  # integrated by an earlier attempt
    ]
    stopped = False

    def blocker(spec: TaskSpec) -> str | None:
        return next((dep for dep in spec.depends_on if dep in failed), None)

    try:  # ruff: ignore[too-many-nested-blocks]
        while True:
            if cancel() and not stopped:
                stopped = True
                for task in running:
                    task.cancel()
            if not stopped:
                for task_id in graph.ready(done):
                    if task_id in started:
                        continue
                    spec = by_id[task_id]
                    if len(running) >= max_parallel:
                        break
                    started.add(task_id)
                    if on_change:
                        on_change(task_id, "running")
                    running[asyncio.create_task(_attempt(worker, spec, retries))] = (
                        task_id
                    )
                # A task whose dependency failed is never "ready"; cascade them.
                changed = True
                while changed:
                    changed = False
                    for spec in graph.tasks:
                        if spec.id in started or spec.id in done:
                            continue
                        if (dep := blocker(spec)) is not None:
                            changed = True
                            started.add(spec.id)
                            failed[spec.id] = f"blocked by failed dependency {dep}"
                            skipped = TaskOutcome(
                                spec.id, "skipped", error=failed[spec.id]
                            )
                            outcomes.append(skipped)
                            if on_outcome:
                                on_outcome(skipped)
                            if on_change:
                                on_change(spec.id, "skipped")
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
                    result = TaskOutcome(
                        task_id, "failed", error=str(exc) or type(exc).__name__
                    )
                if result.status == "complete" and on_complete is not None:
                    try:
                        integrated = await on_complete(result)
                    except Exception as exc:
                        result = TaskOutcome(
                            task_id,
                            "failed",
                            result.worktree,
                            result.commit,
                            str(exc) or type(exc).__name__,
                        )
                        stopped = True
                    else:
                        if not integrated:
                            result = TaskOutcome(
                                task_id,
                                "failed",
                                result.worktree,
                                result.commit,
                                "integration failed",
                            )
                            stopped = True
                outcomes.append(result)
                if result.status == "complete":
                    done.add(task_id)
                else:
                    failed[task_id] = result.error or result.status
                    if result.status == "cancelled":
                        stopped = True
                if on_outcome:
                    on_outcome(result)
                if on_change:
                    on_change(task_id, result.status)
    finally:
        if running:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
    return tuple(outcomes)
