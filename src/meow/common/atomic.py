"""Atomic file replacement that tolerates transient Windows sharing errors."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

# Stdlib only: the installer imports this before any dependency is installed.
logger = logging.getLogger(__name__)

# ACCESS_DENIED, SHARING_VIOLATION, LOCK_VIOLATION: another process (antivirus,
# indexer, a reader) briefly holds the target open.
_RETRYABLE_WINERRORS = frozenset({5, 32, 33})


def _retryable(error: OSError) -> bool:
    return isinstance(error, PermissionError) or (
        getattr(error, "winerror", None) in _RETRYABLE_WINERRORS
    )


def replace_with_retry(  # ruff: ignore[too-many-arguments]
    source: Path,
    target: Path,
    *,
    attempts: int = 10,
    delay: float = 0.05,
    max_delay: float = 2.0,
) -> None:
    """`os.replace` with exponential backoff; the last error is re-raised."""
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if not _retryable(error) or attempt == attempts:
                raise
            logger.warning(
                "atomic_replace_retry target=%s attempt=%d winerror=%s",
                target,
                attempt,
                getattr(error, "winerror", None),
            )
            time.sleep(delay)
            delay = min(delay * 2, max_delay)


def move_directory(source: Path, target: Path) -> None:
    """Move a directory to a free TARGET, retrying, then copying as a last resort.

    Windows refuses to rename a folder while any process holds a file inside it
    open (antivirus scanning fresh executables, the search indexer). Copying
    only needs read access, so it still works when the rename never does.
    """
    try:
        replace_with_retry(source, target, attempts=12)
        return
    except PermissionError:
        if target.exists():
            raise
    try:
        shutil.copytree(source, target, symlinks=True)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    shutil.rmtree(source, ignore_errors=True)
