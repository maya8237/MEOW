"""Commit and push a verified run from an eligible checkout."""

import re
import subprocess
from pathlib import Path

from meow.execution.run_failures import classify_failure, recognised_transient
from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled, check_cancel, delivery_lock
from meow.infrastructure.worktree import _is_linked_worktree, _run_git
from meow.infrastructure.worktree_names import result_branch
from meow.infrastructure.worktree_setup import is_secret_like
from meow.project.config_files import SHARED_PROJECT_PATHS

# An automatic run must fail a stuck push (credential prompt, hook, network)
# rather than wait on it forever.
_GIT_TIMEOUT = 600
_FALLBACK_IDENTITY = ("-c", "user.name=MEOW", "-c", "user.email=meow@local")
_NO_IDENTITY = re.compile(
    r"unable to auto-detect|identity unknown|empty ident|tell me who you are", re.I
)


class GitCommandError(RuntimeError):
    """A git command failed; `detail` is git's own output, without the
    command line, branch or remote URL meow passed to it."""

    def __init__(self, command: str, detail: str) -> None:
        super().__init__(f"git {command} failed: {detail}")
        self.detail = detail


def _git(
    directory: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    result = _run_git(["git", *args], cwd=directory, timeout=_GIT_TIMEOUT)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitCommandError(" ".join(args), detail)
    return result


def _checked_out_branch(directory: Path, fallback_branch: str | None = None) -> str:
    branch = _git(directory, "branch", "--show-current").stdout.strip()
    if not branch:
        if not fallback_branch:
            raise RuntimeError("Automatic delivery requires a checked-out branch")
        _git(directory, "switch", "-c", fallback_branch)
        branch = fallback_branch
    return branch


def _branch_and_remote(
    directory: Path, fallback_branch: str | None = None
) -> tuple[str, str]:
    branch = _checked_out_branch(directory, fallback_branch)
    if "origin" not in _git(directory, "remote").stdout.splitlines():
        raise RuntimeError("No origin remote configured for automatic push")
    return branch, "origin"


def _unstage_secret_like(directory: Path) -> None:
    staged = _git(directory, "diff", "--cached", "--name-only", "-z").stdout.split("\0")
    secrets = [path for path in staged if is_secret_like(Path(path).name)]
    if secrets:
        _git(directory, "reset", "-q", "--", *secrets)


def _commit(directory: Path, message: list[str]) -> None:
    committed = _git(directory, "commit", *message, check=False)
    if committed.returncode and _NO_IDENTITY.search(committed.stderr):
        # No git identity here (fresh CI container): commit as MEOW.
        _git(directory, *_FALLBACK_IDENTITY, "commit", *message)
    elif committed.returncode:
        detail = committed.stderr.strip() or committed.stdout.strip()
        raise GitCommandError("commit", detail)


def commit_worktree(active: Path, message: list[str]) -> str:
    """Commit everything shareable in `active` and return its HEAD sha.

    Stages all changes, keeps MEOW runtime/local state and secret-like files
    out while retaining shared project definitions, and commits only when
    something is staged, so a clean tree just returns the current HEAD.
    """
    _git(active, "add", "-A")
    staged = _git(active, "diff", "--cached", "--name-only", "-z").stdout.split("\0")
    runtime = [
        name
        for name in staged
        if name.startswith(".meow/")
        and not any(
            Path(name).is_relative_to(shared) for shared in SHARED_PROJECT_PATHS
        )
    ]
    if runtime:
        _git(active, "reset", "-q", "--", *runtime)
    # Shared config is staged even under an ignore rule; the update pass also
    # handles its deletion without bringing runtime state into the commit.
    _git(active, "add", "-f", "--", ".meow/config.toml", check=False)
    _git(active, "add", "-u", "--", ".meow/config.toml", check=False)
    _unstage_secret_like(active)
    if _git(active, "diff", "--cached", "--quiet", check=False).returncode:
        _commit(active, message)
    return _git(active, "rev-parse", "HEAD").stdout.strip()


def _last_pushed(active: Path, recorded: str, remote: str, branch: str) -> str:
    """The journaled pushed sha, else the remote-tracking ref, else ''."""
    for candidate in (recorded, f"refs/remotes/{remote}/{branch}"):
        if not candidate:
            continue
        found = _git(
            active,
            "rev-parse",
            "--verify",
            "-q",
            f"{candidate}^{{commit}}",
            check=False,
        )
        if not found.returncode:
            return found.stdout.strip()
    return ""


def push_fix(store: RunStore, run_id: str, message: str) -> str | None:
    """Commit and push a pipeline fix on the run's own branch.

    Pushes whenever HEAD differs from the last pushed sha (so an earlier
    unpushed or fixer-made commit is delivered); `None` (no push) when equal.
    Raises `RuntimeError` when the checked-out branch is not the run's branch.
    The caller journals the returned sha.
    """
    with delivery_lock(store, run_id):
        record = store.load(run_id)
        active = Path(record.worktree)
        branch, remote = _branch_and_remote(active)
        if branch != record.delivery.get("branch", branch):
            raise RuntimeError("checked-out branch changed")
        after = commit_worktree(active, ["-m", message])
        recorded = str(record.delivery.get("commit") or "")
        if after == _last_pushed(active, recorded, remote, branch):
            return None
        _git(active, "push", "-u", remote, branch)
        return after


def deliver_verified_run(
    store: RunStore, run_id: str, *, commit: bool | None = None, push: bool = True
) -> bool:
    """Hold the run's delivery lock until commit/push has finished."""
    with delivery_lock(store, run_id):
        return _deliver_verified_run(store, run_id, commit=commit, push=push)


def _deliver_verified_run(  # ruff: ignore[too-many-statements, complex-structure, too-many-branches]
    store: RunStore, run_id: str, *, commit: bool | None = None, push: bool = True
) -> bool:
    """Deliver after verified checks when enabled, defaulting to linked worktrees.

    Return False when delivery was not requested for an in-place run. A Git
    failure keeps the checkout and journal for manual recovery.
    """
    record = store.load(run_id)
    if record.phase != "checks_finished":
        raise ValueError("Delivery requires current passing checks")
    active = Path(record.worktree)
    if not (_is_linked_worktree(active) if commit is None else commit):
        return False
    try:
        check_cancel(store, run_id)
        if _git(active, "branch", "--show-current").stdout.strip() == "":
            result_branch(active, record.results.get("feature_name") or record.request)
        if push:
            branch, remote = _branch_and_remote(active)
        else:
            branch, remote = _checked_out_branch(active), None
        previous = record.delivery
        if push and previous.get("pushed") and previous.get("commit"):
            return True
        started = {**previous, "branch": branch}
        if remote:
            started["remote"] = remote
        else:
            started["push"] = False
        store.transition(run_id, "delivery_started", branch=branch, delivery=started)
        check_cancel(store, run_id)
        message = ["-m", f"MEOW run {run_id}: {record.request[:72]}"]
        onboarded = record.results.get("onboarding", {}).get("files")
        if onboarded:
            message += ["-m", "Onboarded: " + ", ".join(onboarded)]
        commit_sha = commit_worktree(active, message)
        store.transition(
            run_id,
            "committed",
            delivery={
                **store.load(run_id).delivery,
                "commit": commit_sha,
                "committed": True,
            },
        )
        if not push:
            return True
        check_cancel(store, run_id)
        _git(active, "push", "-u", remote, branch)
        store.transition(
            run_id,
            "delivered",
            delivery={**store.load(run_id).delivery, "pushed": True},
        )
        return True
    except RunCancelled:
        raise
    except (OSError, RuntimeError) as exc:
        _settle_delivery_failure(store, run_id, exc)
        raise


# Git's own push/commit output (never the command line, branch or URL):
# what a retry cannot fix vs. a network hiccup. Patterns are anchored to
# git/server phrasing so a branch or repository name cannot match them.
_GIT_AUTH = re.compile(
    r"Authentication failed|could not read (?:Username|Password)"
    r"|Permission denied \(publickey|returned error: 40[13]\b|invalid credentials",
    re.I,
)
_GIT_POLICY = re.compile(
    r"! \[(?:remote )?rejected\]|hook declined|protected branch"
    r"|\(non-fast-forward\)|not allowed to push",
    re.I,
)
_GIT_TRANSIENT = re.compile(
    r"Could not resolve host|Connection (?:reset|refused|timed out)|RPC failed"
    r"|early EOF|unable to access|returned error: 5\d\d\b"
    r"|remote end hung up unexpectedly|Operation timed out"
    r"|Network is unreachable|No route to host|kex_exchange_identification"
    r"|index\.lock'?: File exists|Unable to create '[^']*\.lock'",
    re.I,
)


def delivery_failure_kind(exc: BaseException) -> tuple[str, str]:
    """`(kind, cause)` for a commit/push failure.

    Exception classes decide first (a git timeout or a known transient class
    parks). Text rules then read only git's own output: auth, rejected or
    hook-declined pushes and protected branches are blocking; recognisable
    network trouble and a stale lock park; anything else is blocking, so a
    resume never replays review just to be rejected again.
    """
    kind, cause = classify_failure(exc)
    if kind == "blocking":
        return kind, cause
    if kind == "resource":
        return kind, cause
    if recognised_transient(exc):
        return "transient", cause
    detail = exc.detail if isinstance(exc, GitCommandError) else ""
    decisions = (
        (bool(_GIT_AUTH.search(detail)), ("blocking", "auth")),
        (bool(_GIT_POLICY.search(detail)), ("blocking", "policy_denied")),
        (bool(_GIT_TRANSIENT.search(detail)), ("transient", cause)),
    )
    return next(
        (decision for hit, decision in decisions if hit),
        ("blocking", "unrecognised_failure"),
    )


def _settle_delivery_failure(store: RunStore, run_id: str, exc: Exception) -> None:
    """A blocking failure ends `delivery_failed`; a transient or resource one
    (network, timeout) parks the run resumable."""
    kind, cause = delivery_failure_kind(exc)
    if kind == "blocking":
        store.transition(
            run_id,
            "delivery_failed",
            last_failure=str(exc),
            results={"failure": {"kind": kind, "cause": cause}},
        )
    else:
        store.park(
            run_id, exc, kind, cause, retry_after=getattr(exc, "retry_after", None)
        )
