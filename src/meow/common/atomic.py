"""Atomic file replacement that tolerates transient Windows sharing errors."""

from __future__ import annotations

import os
import time
from pathlib import Path

from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)

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
                "atomic_replace_retry",
                target=str(target),
                attempt=attempt,
                winerror=getattr(error, "winerror", None),
            )
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
