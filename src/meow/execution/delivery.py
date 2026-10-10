"""Commit and push a verified run from an eligible checkout."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled, check_cancel, delivery_lock


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    branch: str
    commit: str | None = None
    remote: str = "origin"
    message: str = ""


def _git(
    directory: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=directory, capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result


def _linked_worktree(directory: Path) -> bool:
    git_dir_result = _git(directory, "rev-parse", "--absolute-git-dir", check=False)
    if git_dir_result.returncode:
        return False
    git_dir = git_dir_result.stdout.strip()
    common = _git(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(git_dir).resolve() != Path(common.stdout.strip()).resolve()


def _branch_and_remote(
    directory: Path, fallback_branch: str | None = None
) -> tuple[str, str]:
    branch = _git(directory, "branch", "--show-current").stdout.strip()
    if not branch:
        if not fallback_branch:
            raise RuntimeError("Automatic delivery requires a checked-out branch")
        _git(directory, "switch", "-c", fallback_branch)
        branch = fallback_branch
    if "origin" not in _git(directory, "remote").stdout.splitlines():
        raise RuntimeError("No origin remote configured for automatic push")
    return branch, "origin"


def deliver_verified_run(
    store: RunStore, run_id: str, *, unattended: bool = False
) -> bool:
    """Hold the run's delivery lock until commit/push has finished."""
    with delivery_lock(store, run_id):
        return _deliver_verified_run(store, run_id, unattended=unattended)


def _deliver_verified_run(  # ruff: ignore[too-many-statements, complex-structure]
    store: RunStore, run_id: str, *, unattended: bool = False
) -> bool:
    """Deliver after verified checks, from a linked worktree or unattended run.

    Return False when delivery was not requested for an in-place run. A Git
    failure keeps the checkout and journal for manual recovery.
    """
    record = store.load(run_id)
    if record.phase != "checks_finished":
        raise ValueError("Delivery requires current passing checks")
    active = Path(record.worktree)
    if not (unattended or _linked_worktree(active)):
        return False
    try:
        check_cancel(store, run_id)
        branch, remote = _branch_and_remote(active, f"meow/{run_id}")
        previous = record.delivery
        if previous.get("pushed") and previous.get("commit"):
            return True
        store.transition(
            run_id,
            "delivery_started",
            branch=branch,
            delivery={**previous, "branch": branch, "remote": remote},
        )
        check_cancel(store, run_id)
        _git(active, "add", "-A")
        _git(active, "reset", "-q", "--", ".meow", check=False)
        # Re-stage the one shareable MEOW file; the update pass also handles
        # its deletion without bringing runtime state back into the commit.
        _git(active, "add", "-f", "--", ".meow/config.toml", check=False)
        _git(active, "add", "-u", "--", ".meow/config.toml", check=False)
        if _git(active, "diff", "--cached", "--quiet", check=False).returncode:
            check_cancel(store, run_id)
            _git(active, "commit", "-m", f"MEOW run {run_id}: {record.request[:72]}")
        commit = _git(active, "rev-parse", "HEAD").stdout.strip()
        store.transition(
            run_id,
            "committed",
            delivery={
                **store.load(run_id).delivery,
                "commit": commit,
                "committed": True,
            },
        )
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
        store.transition(run_id, "delivery_failed", last_failure=str(exc))
        raise
