"""Retryable-by-default failure classification for saved runs.

A run stops for good (`failed`) only on a cause in `BLOCKING_CAUSES`; every
other exception parks it resumable (see `RunStore.park`).
"""

import errno
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from meow.execution.run_state import (
    _ACTIVE_PHASES,
    TERMINAL_PHASES,
    RunStateError,
)
from meow.infrastructure.worktree import StartPointError

if TYPE_CHECKING:
    from meow.execution.run_state import RunRecord

FailureKind = Literal["blocking", "resource", "transient"]

BLOCKING_CAUSES = frozenset({
    "invalid_config",
    "invalid_run_state",
    "cli_not_found",
    "git_missing",
    "auth",
    "policy_denied",
    "invalid_ref",
    "invalid_input",
    "model_unavailable",
    "no_progress",
})
# Sources `meow resume --continue` can drive; others are rerun from scratch.
RESUMABLE_SOURCES = frozenset({"prompt", "jira", "native", "orchestrator"})
# ERROR_NOT_ENOUGH_MEMORY, ERROR_OUTOFMEMORY, ERROR_NO_SYSTEM_RESOURCES,
# ERROR_COMMITMENT_LIMIT (paging file too small).
_RESOURCE_WINERRORS = frozenset({8, 14, 1450, 1455})
_RESOURCE_TEXT = re.compile(r"\[WinError (?:8|14|1450|1455)\]")
_POLICY_DENIED = re.compile(r"permission denied by policy", re.I)
# A --work-dir outside any Git checkout is a bad meow argument, not a crash.
_NOT_A_REPO = re.compile(r"fatal: not a git repository", re.I)
_SAFE_ARG = re.compile(r"[\w@%+=:,./\\-]+")


class TransientRunError(RuntimeError):
    """A retryable failure; `retry_after` (seconds) overrides the backoff."""

    def __init__(
        self, message: str, *, cause: str, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.retry_after = retry_after


class BlockingRunError(RuntimeError):
    """A failure no resume can fix; the run ends `failed` with `cause`."""

    def __init__(self, message: str, *, cause: str) -> None:
        super().__init__(message)
        self.cause = cause


class InvalidConfigError(BlockingRunError, ValueError):
    """Configuration that cannot load; still a ValueError for old callers."""

    def __init__(self, message: str) -> None:
        super().__init__(message, cause="invalid_config")


def _is_git_missing(exc: BaseException) -> bool:
    name = Path(str(getattr(exc, "filename", None) or "")).name.lower()
    return isinstance(exc, FileNotFoundError) and name in {"git", "git.exe"}


def _blocking_cause(exc: BaseException) -> str | None:
    from claude_agent_sdk import CLINotFoundError

    from meow.infrastructure.worktree_setup import WorktreeSetupError

    own = getattr(exc, "cause", None)
    text = str(exc)
    checks = (
        (isinstance(exc, BlockingRunError), own),
        (isinstance(exc, RunStateError), "invalid_run_state"),
        (isinstance(exc, CLINotFoundError), "cli_not_found"),
        # ApiFailure (agents/api_errors.py), duck-typed.
        (getattr(exc, "kind", None) == "blocking" and isinstance(own, str), own),
        (
            isinstance(exc, StartPointError) and not exc.transient,
            "invalid_ref",
        ),
        (
            isinstance(exc, WorktreeSetupError) and own == "policy_denied",
            "policy_denied",
        ),
        (_is_git_missing(exc), "git_missing"),
        (bool(_POLICY_DENIED.search(text)), "policy_denied"),
        (bool(_NOT_A_REPO.search(text)), "invalid_input"),
    )
    return next((cause for hit, cause in checks if hit), None)


def _is_resource(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, MemoryError)
            or getattr(current, "winerror", None) in _RESOURCE_WINERRORS
            or (isinstance(current, OSError) and current.errno == errno.ENOMEM)
            or _RESOURCE_TEXT.search(str(current))
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def classify_failure(exc: BaseException) -> tuple[FailureKind, str]:
    """Return `(kind, cause)`: blocking only for the known-permanent cases."""
    if blocking := _blocking_cause(exc):
        return "blocking", blocking
    own = getattr(exc, "cause", None)
    if _is_resource(exc):
        return "resource", own if isinstance(own, str) and own else "resource"
    return "transient", own if isinstance(own, str) and own else "error"


def _head(worktree: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _phase_before(record: "RunRecord") -> str:
    if record.phase == "parked":
        return str((record.park or {}).get("phase_before") or record.phase)
    return record.phase


def progress_fingerprint(record: "RunRecord") -> str:
    """`phase_before|round|HEAD|active steps`; equal means no progress.

    Active steps count distinct `(phase, round)` pairs, so replaying the same
    step after a resume is not progress while reaching a new one is.
    """
    steps = {
        (item.get("phase"), item.get("round"))
        for item in record.transitions
        if item.get("phase") in _ACTIVE_PHASES and item.get("phase") != "resuming"
    }
    return "|".join((
        _phase_before(record),
        str(record.round),
        _head(record.worktree),
        str(len(steps)),
    ))


def _shell_arg(value: str) -> str:
    return value if _SAFE_ARG.fullmatch(value) else f'"{value}"'


def next_command(record: "RunRecord") -> str | None:
    """The `meow resume` command that continues this run, or None."""
    if record.source not in RESUMABLE_SOURCES or record.phase in TERMINAL_PHASES:
        return None
    repair = " --repair" if record.source == "native" else ""
    return (
        f"meow resume {record.id} --continue{repair} "
        f"--work-dir {_shell_arg(record.repo)}"
    )
