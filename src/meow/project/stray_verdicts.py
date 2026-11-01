"""Stray copies of a verdict file written outside its expected path.

A weaker model sometimes writes ``tester.stage.md`` to a stale ``plans/`` copy
instead of the absolute path it was given. These helpers clear untracked
leftovers before a run and adopt a copy written during it afterwards.
"""

import os
import subprocess
from pathlib import Path

from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)

SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv"})
# Filesystem timestamps can trail time.time() (coarse or cached clocks).
MTIME_SLACK = 2.0


def stray_copies(worktree: Path, expected: Path) -> list[Path]:
    """Same-named files elsewhere under ``worktree``, never ``expected`` itself."""
    expected = expected.resolve()
    found = []
    for root, dirs, files in os.walk(worktree):
        dirs[:] = [name for name in dirs if name not in SKIPPED_DIRS]
        if expected.name in files:
            path = Path(root, expected.name)
            if path.resolve() != expected:
                found.append(path)
    return found


def recover_stray(worktree: Path, expected: Path, started: float) -> Path | None:
    """The newest stray copy modified since ``started`` (a time.time())."""
    fresh = []
    for path in stray_copies(worktree, expected):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= started - MTIME_SLACK:
            fresh.append((mtime, path))
    return max(fresh)[1] if fresh else None


def _is_untracked(worktree: Path, path: Path) -> bool:
    """True only when git confirms ``path`` is not tracked; any doubt is False."""
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "ls-files",
                "--error-unmatch",
                "--",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return done.returncode == 1 and "did not match" in done.stderr


def clear_untracked_strays(worktree: Path, expected: Path) -> list[Path]:
    """Delete stray copies git does not track, so none can mislead the agent."""
    removed = []
    for path in stray_copies(worktree, expected):
        if not _is_untracked(worktree, path):
            continue
        try:
            path.unlink()
        except OSError:
            continue
        logger.info("stray_verdict_removed", path=str(path))
        removed.append(path)
    return removed


def adopt_stray(worktree: Path, expected: Path, started: float) -> Path | None:
    """Move a stray copy written during this run to ``expected``; return its origin."""
    stray = recover_stray(worktree, expected, started)
    if stray is None:
        return None
    try:
        expected.parent.mkdir(parents=True, exist_ok=True)
        stray.replace(expected)
    except OSError:
        return None
    logger.warning("stray_verdict_adopted", stray=str(stray), expected=str(expected))
    return stray


def read_verdict(
    worktree: Path, expected: Path, started: float
) -> tuple[str | None, Path | None]:
    """``expected``'s text, adopting a stray written since ``started`` when it
    is missing; also returns the adopted stray's origin, if any."""
    try:
        return expected.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError):
        pass
    stray = adopt_stray(worktree, expected, started)
    if stray is None:
        return None, None
    try:
        return expected.read_text(encoding="utf-8"), stray
    except (OSError, UnicodeError):
        return None, stray
