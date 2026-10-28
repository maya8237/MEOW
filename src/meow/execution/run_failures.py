"""Retryable-by-default failure classification for saved runs.

A run stops for good (`failed`) only on a cause in `BLOCKING_CAUSES`; every
other exception parks it resumable (see `RunStore.park`).
"""

import errno
import re
import subprocess
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Literal

from meow.execution.run_state import (
    _ACTIVE_PHASES,
    TERMINAL_PHASES,
    RunStateError,
)
from meow.infrastructure.worktree import (
    DirtyWorkingTreeError,
    GitTimeoutError,
    StartPointError,
)

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
# A repository without the remote a push needs: setup, not a crash.
# (Not "does not appear to be a git repository": an unreachable path or
# share produces it too.)
_NO_REMOTE = re.compile(r"No '?origin'? remote configured|No such remote", re.I)
# Sharing/lock violations, access denied by a scanner, semaphore timeout,
# network name gone: Windows I/O that a later retry usually gets past.
_TRANSIENT_WINERRORS = frozenset({5, 32, 33, 64, 121})
_TRANSIENT_ERRNOS = frozenset({
    errno.EAGAIN,
    errno.EBUSY,
    errno.EINTR,
    errno.EIO,
    errno.ENOSPC,
    errno.ETIMEDOUT,
    errno.ECONNRESET,
    errno.ECONNREFUSED,
    errno.ECONNABORTED,
    errno.EPIPE,
    errno.ENETDOWN,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
})
# OSError subclasses that describe the input (a wrong path), not the moment.
_INPUT_OSERRORS = (
    FileNotFoundError,
    FileExistsError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
)
_SAFE_ARG = re.compile(r"[\w@%+=:,./-]+")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]|\\\\")


DRIVER_BUSY = "driver_busy"
# About one status tick (`*/7`): the manager polls again once the owner may be done.
DRIVER_BUSY_RETRY_AFTER = 420


def is_driver_busy(exc: BaseException) -> bool:
    """A refusal because a live driver owns the orchestrator: not a failure."""
    from meow.execution.orchestration.store import DriverBusyError

    return isinstance(exc, DriverBusyError)


class TransientRunError(RuntimeError):
    """A retryable failure; `retry_after` (seconds) overrides the backoff."""

    def __init__(
        self, message: str, *, cause: str, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.retry_after = retry_after


class WindowsQueryTimeout(TransientRunError, ValueError):
    """A Windows tool (`schtasks`, ...) outlasted its timeout; try again later."""

    def __init__(self, message: str) -> None:
        super().__init__(message, cause="windows_query_timeout")


class BlockingRunError(RuntimeError):
    """A failure no resume can fix; the run ends `failed` with `cause`."""

    def __init__(self, message: str, *, cause: str) -> None:
        super().__init__(message)
        self.cause = cause


class InvalidConfigError(BlockingRunError, ValueError):
    """Configuration that cannot load; still a ValueError for old callers."""

    def __init__(self, message: str) -> None:
        super().__init__(message, cause="invalid_config")


def park_on_check_timeout(results: list) -> None:
    """A required check that timed out (after its rerun) is infrastructure
    work, not a code failure: park the run instead of burning a fix round."""
    timed_out = [r for r in results if r.required and r.timed_out]
    if timed_out:
        names = ", ".join(f"{r.kind} `{r.command}`" for r in timed_out)
        raise TransientRunError(
            f"required check timed out twice: {names}", cause="check_timeout"
        )


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
        (bool(_NO_REMOTE.search(text)), "invalid_input"),
        (isinstance(exc, DirtyWorkingTreeError), "invalid_input"),
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


def _transient_os_error(exc: OSError) -> bool:
    if getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS:
        return True
    if exc.errno in _TRANSIENT_ERRNOS or isinstance(exc, ConnectionError):
        return True
    # A bare OSError (no errno subclass) is environmental I/O, not bad input.
    return not isinstance(exc, _INPUT_OSERRORS) and exc.errno is None


def _transient_leaf(exc: BaseException) -> bool:  # ruff: ignore[too-many-return-statements]
    from claude_agent_sdk import CLIConnectionError, ProcessError

    if isinstance(exc, (TransientRunError, ProcessError, CLIConnectionError)):
        return True
    if getattr(exc, "cause", None) == "setup_timeout":
        return True
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired, GitTimeoutError)):
        return True
    # ApiFailure (agents/api_errors.py), duck-typed: rate limit, overload, ...
    if getattr(exc, "kind", None) in {"transient", "resource"} and isinstance(
        getattr(exc, "cause", None), str
    ):
        return True
    return isinstance(exc, OSError) and _transient_os_error(exc)


def recognised_transient(exc: BaseException) -> bool:
    """True when the failure is a known-retryable class: API/SDK process,
    timeout, OS resource or transient I/O, in its `raise ... from` chain or
    exception group. Used where no run record bounds the retries, so an
    unrecognised exception is not retried blindly.
    """
    if _blocking_cause(exc):
        return False
    return _is_resource(exc) or any(_transient_leaf(item) for item in _leaves(exc))


def _leaves(exc: BaseException):
    """Every exception in groups and explicit `raise ... from` chains."""
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
            continue
        yield current
        if current.__cause__ is not None:  # an explicit wrapper, not context
            pending.append(current.__cause__)


def failure_retry_after(exc: BaseException) -> float | None:
    """The first `retry_after` (seconds) an exception, its group members or
    its `raise ... from` causes ask for."""
    for item in _leaves(exc):
        value = getattr(item, "retry_after", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def classify_failure(exc: BaseException) -> tuple[FailureKind, str]:
    """Return `(kind, cause)`: blocking only for the known-permanent cases.

    A wrapper that only reports "the issue was not solved" is classified by
    the failure it wraps, so a 429 parks and an auth error still blocks.
    """
    from meow.integrations.issue_solver import IssueUnresolvedError

    if isinstance(exc, IssueUnresolvedError) and exc.__cause__ is not None:
        return classify_failure(exc.__cause__)
    # A blocking cause anywhere in the groups and `raise ... from` chain wins.
    leaves = list(_leaves(exc))
    for item in leaves:
        if blocking := _blocking_cause(item):
            return "blocking", blocking
    own = next(
        (
            cause
            for item in leaves
            if isinstance(cause := getattr(item, "cause", None), str) and cause
        ),
        None,
    )
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


_RUN_ID_ATTR = "meow_run_id"


def attach_run_id(exc: BaseException, run_id: str) -> None:
    """Remember which run an escaping exception settled (read at exit)."""
    with suppress(AttributeError, TypeError):
        if getattr(exc, _RUN_ID_ATTR, None) is None:
            setattr(exc, _RUN_ID_ATTR, run_id)


def run_id_of(exc: BaseException | None) -> str | None:
    value = getattr(exc, _RUN_ID_ATTR, None)
    return value if isinstance(value, str) else None


def shell_arg(value: str) -> str:
    """A path as one shell word. Git Bash eats backslashes (quoted or not), so
    Windows paths go out with forward slashes."""
    if _WINDOWS_PATH.match(value):
        value = PureWindowsPath(value).as_posix()
    return value if _SAFE_ARG.fullmatch(value) else f'"{value}"'


def never_prepared(record: "RunRecord") -> bool:
    """True for a run that stopped before its worktree and branch existed (the
    record still says `preparing`)."""
    phases = {step.get("phase") for step in record.transitions}
    return record.branch == "preparing" and "prepared" not in phases


def next_command(record: "RunRecord") -> str | None:
    """The `meow resume` command that continues this run, or None."""
    if record.source not in RESUMABLE_SOURCES or record.phase in TERMINAL_PHASES:
        return None
    if never_prepared(record) and not record.background.get("argv"):
        return None  # nothing to validate or replay, and no command to restart
    if record.results.get("intent") == "plan":  # stops at planned on purpose
        return None
    repair = " --repair" if record.source == "native" else ""
    return (
        f"meow resume {record.id} --continue{repair} "
        f"--work-dir {shell_arg(record.repo)}"
    )
