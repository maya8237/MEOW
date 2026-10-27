import asyncio

from meow.execution import queue_runner
from meow.execution.queue_state import QueueStore
from meow.execution.run_failures import BlockingRunError, TransientRunError
from meow.execution.run_state import RunStore


def test_queue_runner_processes_fifo_and_pauses_on_blocking_failure(
    tmp_path, monkeypatch
):
    store = QueueStore(tmp_path)
    first = store.enqueue("first")
    second = store.enqueue("second")
    seen = []

    async def fake_run(*args, **kwargs):
        await asyncio.sleep(0)
        seen.append(args[2])
        if len(seen) == 2:  # ruff: ignore[magic-value-comparison]
            raise BlockingRunError("blocked", cause="invalid_input")

    monkeypatch.setattr(queue_runner, "run_sprint", fake_run)
    assert asyncio.run(queue_runner.run_queue(tmp_path)) == 1
    records = {item.id: item for item in store.list()}
    assert seen == ["first", "second"]
    assert records[first.id].status == "completed"
    assert records[second.id].status == "failed"


def test_second_queue_worker_does_not_run_items(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    store.enqueue("task")
    lock = queue_runner.QueueWorkerLock(store.directory)
    assert lock.acquire()
    called = False

    async def fake_run(*_args, **_kwargs):
        await asyncio.sleep(0)
        nonlocal called
        called = True

    monkeypatch.setattr(queue_runner, "run_sprint", fake_run)
    assert asyncio.run(queue_runner.run_queue(tmp_path)) == 0
    assert called is False
    lock.release()


def test_item_orphaned_by_a_killed_worker_is_reported_parked_not_stuck(
    tmp_path, monkeypatch
):
    store = QueueStore(tmp_path)
    orphan = store.enqueue("died mid-run")
    later = store.enqueue("next")
    store.claim(orphan.id)  # the worker that owned it was SIGKILLed
    seen = []

    async def fake_run(*args, **_kwargs):
        await asyncio.sleep(0)
        seen.append(args[2])

    monkeypatch.setattr(queue_runner, "run_sprint", fake_run)
    assert asyncio.run(queue_runner.run_queue(tmp_path)) == 0
    records = {item.id: item for item in store.list()}
    assert records[orphan.id].status == "parked"
    assert "worker" in records[orphan.id].error
    assert records[later.id].status == "completed"
    assert seen == ["next"]


def _parking_sprint(runs, seen):
    """A fake sprint: one run parks its record, one fails before any record."""

    async def fake_run(*args, **kwargs):
        await asyncio.sleep(0)
        seen.append(args[2])
        if args[2] == "crashes":
            run_id = runs.create(
                source="queue",
                request=args[2],
                repo=runs.directory.parent.parent,
                worktree=runs.directory.parent.parent,
                branch="b",
            ).id
            kwargs["on_created"](run_id)
            runs.park(run_id, "boom", "transient", "error")
            raise RuntimeError("boom")
        if args[2] == "rate limited":
            raise TransientRunError("429", cause="rate_limit")

    return fake_run


def test_queue_continues_past_parked(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    first = store.enqueue("crashes")
    second = store.enqueue("rate limited")
    third = store.enqueue("works")
    runs = RunStore(tmp_path)
    seen = []
    monkeypatch.setattr(queue_runner, "run_sprint", _parking_sprint(runs, seen))

    assert asyncio.run(queue_runner.run_queue(tmp_path)) == 0

    records = {item.id: item for item in store.list()}
    assert seen == ["crashes", "rate limited", "works"]
    assert records[first.id].status == "parked"
    assert runs.load(records[first.id].run_id).phase == "parked"
    assert records[second.id].status == "parked"
    assert records[third.id].status == "completed"


def test_requeue_parked_returns_items_to_pending(tmp_path):
    store = QueueStore(tmp_path)
    first = store.enqueue("a")
    store.enqueue("b")
    store.update(first.id, status="parked", error="boom")
    assert [item.id for item in store.requeue_parked()] == [first.id]
    requeued = {item.id: item for item in store.list()}[first.id]
    assert (requeued.status, requeued.error) == ("pending", None)
    assert store.pending()[0].id == first.id


def test_queue_pauses_when_the_run_record_failed(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    first = store.enqueue("blocked")
    later = store.enqueue("later")
    runs = RunStore(tmp_path)

    async def fake_run(*args, **kwargs):
        await asyncio.sleep(0)
        run_id = runs.create(
            source="queue",
            request=args[2],
            repo=tmp_path,
            worktree=tmp_path,
            branch="b",
        ).id
        kwargs["on_created"](run_id)
        runs.transition(run_id, "failed")
        raise RuntimeError("bad config")

    monkeypatch.setattr(queue_runner, "run_sprint", fake_run)
    assert asyncio.run(queue_runner.run_queue(tmp_path)) == 1
    records = {item.id: item for item in store.list()}
    assert records[first.id].status == "failed"
    assert records[later.id].status == "pending"
