"""Serial execution of durable queue items."""

from pathlib import Path

from meow.execution.queue_state import QueueItem, QueueStore, QueueWorkerLock
from meow.execution.sprint_runner import run_sprint


def _report_orphans(store: QueueStore) -> None:
    for orphan in store.recover_orphans():
        print(f"Queue item {orphan.id} was interrupted: {orphan.request}")


async def _run_item(working_dir: Path, store: QueueStore, item: QueueItem) -> bool:
    """Run one claimed item; False when it failed and the queue should pause."""
    try:
        await run_sprint(
            working_dir,
            item.feature_name,
            item.request,
            use_worktree=False,
            record_root=working_dir,
            source="queue",
        )
    except BaseException as exc:
        store.update(item.id, status="failed", error=str(exc) or "interrupted")
        if not isinstance(exc, Exception):
            raise  # Ctrl-C / cancellation stops the worker
        print(f"Queue paused on {item.id}: {exc}")
        return False
    store.update(item.id, status="completed")
    return True


async def run_queue(working_dir: Path) -> int:
    store = QueueStore(working_dir)
    lock = QueueWorkerLock(store.directory)
    if not lock.acquire():
        print("Queue worker already running.")
        return 0
    try:
        _report_orphans(store)
        while pending := store.pending():
            item = store.claim(pending[0].id)
            if not await _run_item(working_dir, store, item):
                return 1
        return 0
    finally:
        lock.release()
