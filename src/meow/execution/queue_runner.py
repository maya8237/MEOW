"""Serial execution of durable queue items."""

from pathlib import Path

from meow.execution.queue_state import QueueStore, QueueWorkerLock
from meow.execution.sprint_runner import run_sprint


async def run_queue(working_dir: Path) -> int:
    store = QueueStore(working_dir)
    lock = QueueWorkerLock(store.directory)
    if not lock.acquire():
        print("Queue worker already running.")
        return 0
    try:
        while True:
            pending = store.pending()
            if not pending:
                return 0
            item = store.claim(pending[0].id)
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
                return 1
            store.update(item.id, status="completed")
    finally:
        lock.release()
