import asyncio

from meow.execution import queue_runner
from meow.execution.queue_state import QueueStore


def test_queue_runner_processes_fifo_and_pauses_on_failure(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    first = store.enqueue("first")
    second = store.enqueue("second")
    seen = []

    async def fake_run(*args, **kwargs):
        await asyncio.sleep(0)
        seen.append(args[2])
        if len(seen) == 2:  # ruff: ignore[magic-value-comparison]
            raise RuntimeError("blocked")

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
