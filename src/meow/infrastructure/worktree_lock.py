"""Serialize Git worktree creation across threads and processes in one repo."""

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path


def _lock(stream, *, unlock: bool = False) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(stream.fileno(), mode, 1)
    else:
        import fcntl

        mode = fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(stream.fileno(), mode)


@contextmanager
def worktree_lock(common_dir: Path):
    # Keep the inode after release: unlinking lets waiters lock different files.
    with (common_dir / "meow-worktree.lock").open("a+b") as stream:
        deadline = time.monotonic() + 600
        while True:
            try:
                _lock(stream)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out waiting for Git worktree creation"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _lock(stream, unlock=True)
