"""Serial execution of durable queue items."""

from pathlib import Path

from meow.execution.queue_state import QueueItem, QueueStore, QueueWorkerLock
from meow.execution.run_failures import classify_failure
from meow.execution.run_state import PARKED_PHASE, RunStateError, RunStore
from meow.execution.sprint_runner import run_sprint


def _report_orphans(store: QueueStore) -> None:
    for orphan in store.recover_orphans():
        print(f"Queue item {orphan.id} was interrupted: {orphan.request}")


def _failure_status(working_dir: Path, run_id: str | None, exc: BaseException) -> str:
    """`parked` when the run parked resumable, `failed` when it is blocked."""
    if run_id is not None:
        try:
            phase = RunStore(working_dir).load(run_id).phase
        except RunStateError:
            phase = None
        if phase == PARKED_PHASE:
            return "parked"
        if phase is not None:
            return "failed"
    return "failed" if classify_failure(exc)[0] == "blocking" else "parked"


async def _run_item(working_dir: Path, store: QueueStore, item: QueueItem) -> bool:
    """Run one claimed item; False when it failed and the queue should pause.

    A parked run (retryable failure) does not pause the queue: the item is
    marked `parked` and `meow resume --parked` requeues it later.
    """
    run_ids: list[str] = []

    def created(run_id: str) -> None:
        run_ids.append(run_id)
        store.update(item.id, run_id=run_id)

    try:
        await run_sprint(
            working_dir,
            item.feature_name,
            item.request,
            use_worktree=False,
            record_root=working_dir,
            source="queue",
            on_created=created,
        )
    except BaseException as exc:
        status = _failure_status(working_dir, (run_ids or [None])[-1], exc)
        store.update(item.id, status=status, error=str(exc) or "interrupted")
        if not isinstance(exc, Exception):
            raise  # Ctrl-C / cancellation stops the worker
        if status == "parked":
            print(f"Queue item {item.id} parked (resumable): {exc}")
            return True
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
