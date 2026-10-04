"""CLI facade for queueing and running repository tasks."""

import asyncio
from pathlib import Path

from meow.execution.queue_runner import run_queue
from meow.execution.queue_state import QueueStore


def queue(
    working_dir: Path,
    request: str | None = None,
    feature_name: str | None = None,
) -> int:
    store = QueueStore(working_dir)
    if request is not None:
        item = store.enqueue(request, feature_name)
        print(f"Queued {item.id}: {item.request}")
        return 0
    return asyncio.run(run_queue(working_dir))
