"""Cooperative, cross-process cancellation for checkpointed feature runs."""

import asyncio
import os
import sys
import time
from collections.abc import Awaitable
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TypeVar

from meow.execution.run_state import TERMINAL_PHASES, RunRecord, RunStore

T = TypeVar("T")


class RunCancelled(RuntimeError):
    """A run stopped after a user cancellation request."""


def _marker(store: RunStore, run_id: str) -> Path:
    return store._path(run_id).with_suffix(".cancel")


def cancel_requested(store: RunStore, run_id: str) -> bool:
    return _marker(store, run_id).is_file()


def clear_cancel(store: RunStore, run_id: str) -> None:
    """Allow an explicitly resumed run to proceed after validation."""
    _marker(store, run_id).unlink(missing_ok=True)


@contextmanager
def delivery_lock(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    store: RunStore, run_id: str
):
    """Serialize cancellation acceptance with the irreversible delivery step."""
    path = store._path(run_id).with_suffix(".delivery-lock")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 30
        if sys.platform == "win32":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Delivery is in progress; retry cancellation"
                        ) from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Delivery is in progress; retry cancellation"
                        ) from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def request_cancel(store: RunStore, run_id: str) -> RunRecord:
    """Atomically request cancellation; never signal an unverified process ID."""
    with delivery_lock(store, run_id):
        record = store.load(run_id)
        if record.phase in TERMINAL_PHASES:
            raise ValueError(f"Run {run_id} is already {record.phase}.")
        marker = _marker(store, run_id)
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return record
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("cancel requested\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record


def check_cancel(store: RunStore, run_id: str) -> None:
    if cancel_requested(store, run_id):
        if store.load(run_id).phase != "cancelled":
            store.transition(run_id, "cancelled", last_failure="Cancellation requested")
        raise RunCancelled(f"Run {run_id} cancelled; worktree and artifacts retained.")


async def cancellable(  # ruff: ignore[non-pep695-generic-function]
    store: RunStore, run_id: str, operation: Awaitable[T]
) -> T:
    """Cancel an awaited agent/check operation when the marker appears."""
    task = asyncio.create_task(operation)
    try:
        check_cancel(store, run_id)
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.25)
            if done:
                check_cancel(store, run_id)
                return await task
            if cancel_requested(store, run_id):
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                check_cancel(store, run_id)
    finally:
        if not task.done():
            task.cancel()
