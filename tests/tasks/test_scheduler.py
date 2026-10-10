"""Parallel task scheduling respects dependencies and cancellation."""

import asyncio
from contextlib import suppress

from meow.tasks.model import TaskGraph, TaskSpec
from meow.tasks.scheduler import TaskOutcome, run_task_graph


def _graph():
    return TaskGraph((
        TaskSpec("api", (), ("src/api",), ()),
        TaskSpec("ui", (), ("src/ui",), ()),
        TaskSpec("integration", ("api", "ui"), ("tests",), ()),
    ))


def test_disjoint_tasks_run_together_and_dependency_waits():
    active = set()
    peak = 0
    started = []

    async def worker(spec):
        nonlocal peak
        active.add(spec.id)
        started.append((spec.id, set(active)))
        peak = max(peak, len(active))
        await asyncio.sleep(0.01)
        active.remove(spec.id)
        return TaskOutcome(spec.id, "complete", f"/tmp/{spec.id}", spec.id)

    outcomes = asyncio.run(run_task_graph(_graph(), worker, max_parallel=2))
    assert peak == len(("api", "ui"))
    assert [outcome.id for outcome in outcomes][-1] == "integration"
    assert all("integration" not in peers for _, peers in started[:2])


def test_failed_worker_blocks_dependents_and_preserves_result():
    async def worker(spec):  # ruff: ignore[unused-async]
        if spec.id == "api":
            return TaskOutcome(spec.id, "failed", "/tmp/api", None, "agent failed")
        return TaskOutcome(spec.id, "complete", f"/tmp/{spec.id}", spec.id)

    outcomes = asyncio.run(run_task_graph(_graph(), worker, max_parallel=2))
    assert any(item.id == "api" and item.status == "failed" for item in outcomes)
    assert not any(item.id == "integration" for item in outcomes)


def test_cancel_prevents_any_worker_start():
    called = []

    async def worker(spec):  # ruff: ignore[unused-async]
        called.append(spec.id)
        return TaskOutcome(spec.id, "complete", "/tmp", spec.id)

    outcomes = asyncio.run(
        run_task_graph(_graph(), worker, max_parallel=2, cancel=lambda: True)
    )
    assert outcomes == ()
    assert called == []


def test_dependent_starts_after_parent_is_integrated():
    integrated = []
    graph = TaskGraph((
        TaskSpec("api", (), ("src/api",), ()),
        TaskSpec("ui", ("api",), ("src/ui",), ()),
    ))

    async def worker(spec):  # ruff: ignore[unused-async]
        if spec.id == "ui":
            assert integrated == ["api"]
        return TaskOutcome(spec.id, "complete", "/tmp", spec.id)

    async def integrate(outcome):  # ruff: ignore[unused-async]
        integrated.append(outcome.id)
        return True

    outcomes = asyncio.run(
        run_task_graph(graph, worker, max_parallel=2, on_complete=integrate)
    )
    assert [item.id for item in outcomes] == ["api", "ui"]


def test_external_cancellation_waits_for_active_workers_to_stop():
    async def scenario():
        started = asyncio.Event()
        stopped = []

        async def worker(spec):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(spec.id)

        runner = asyncio.create_task(run_task_graph(_graph(), worker, max_parallel=2))
        await started.wait()
        runner.cancel()
        with suppress(asyncio.CancelledError):
            await runner
        assert sorted(stopped) == ["api", "ui"]

    asyncio.run(scenario())
