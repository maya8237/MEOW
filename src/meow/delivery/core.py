"""Commit and push a verified run from an eligible checkout."""

import subprocess
from pathlib import Path

from meow.run_state import RunStore


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


def deliver_verified_run(  # ruff: ignore[complex-structure, too-many-statements]
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
        remotes = _git(active, "remote").stdout.splitlines()
        if "origin" not in remotes:
            raise RuntimeError("No origin remote configured for automatic push")
        branch = _git(active, "branch", "--show-current").stdout.strip()
        if not branch:
            branch = f"meow/{run_id}"
            _git(active, "switch", "-c", branch)
        store.transition(run_id, "delivering", branch=branch)
        _git(active, "add", "-A")
        _git(active, "reset", "-q", "--", ".meow", check=False)
        if _git(active, "diff", "--cached", "--quiet", check=False).returncode:
            _git(active, "commit", "-m", f"MEOW run {run_id}: {record.request[:72]}")
        commit = _git(active, "rev-parse", "HEAD").stdout.strip()
        _git(active, "push", "-u", "origin", branch)
        store.transition(
            run_id,
            "delivered",
            delivery={"branch": branch, "commit": commit, "remote": "origin"},
        )
        return True
    except (OSError, RuntimeError) as exc:
        store.transition(run_id, "delivery_failed", last_failure=str(exc))
        raise
