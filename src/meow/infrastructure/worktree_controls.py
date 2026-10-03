"""Read-only worktree inspection and conservative cleanup by run ID."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from meow.execution.run_state import RunStore


class WorktreeSafetyError(RuntimeError):
    """A worktree cannot be removed without risking user work."""


@dataclass(frozen=True)
class WorktreeInspection:
    run_id: str
    path: Path
    branch: str
    phase: str
    registered: bool
    dirty: bool | None
    unpushed: bool | None
    safe_to_clean: bool
    reasons: tuple[str, ...]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _registered(repo: Path) -> set[Path]:
    result = _git(repo, "worktree", "list", "--porcelain")
    if result.returncode:
        raise WorktreeSafetyError(result.stderr.strip() or "git worktree list failed")
    return {
        Path(line[9:]).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }


def inspect_worktree(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    repo: Path, run_id: str
) -> WorktreeInspection:
    repo = repo.resolve()
    record = RunStore(repo).load(run_id)
    path = Path(record.worktree).resolve()
    root = (repo / ".worktrees").resolve()
    reasons: list[str] = []
    if Path(record.repo).resolve() != repo:
        reasons.append("run belongs to another repository")
    if path.parent != root:
        reasons.append("path is outside the MEOW worktree root")
    registered = path in _registered(repo)
    if not registered:
        reasons.append("worktree is not registered with Git")
    if not path.is_dir():
        reasons.append("worktree directory is missing")
    dirty: bool | None = None
    unpushed: bool | None = None
    if registered and path.is_dir():
        common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        expected = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if (
            common.returncode
            or expected.returncode
            or Path(common.stdout.strip()).resolve()
            != Path(expected.stdout.strip()).resolve()
        ):
            reasons.append("worktree belongs to another repository")
        status = _git(path, "status", "--porcelain", "--untracked-files=all")
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        if dirty is None or dirty:
            reasons.append("worktree has uncommitted or untracked files")
        branch = _git(path, "branch", "--show-current")
        if branch.returncode or not branch.stdout.strip():
            reasons.append("worktree is detached or branch is unavailable")
        upstream = _git(
            path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        )
        if upstream.returncode:
            unpushed = None
            reasons.append("branch has no upstream; push state is unknown")
        else:
            ahead = _git(path, "rev-list", "--count", "@{upstream}..HEAD")  # ruff: ignore[missing-f-string-syntax]
            if ahead.returncode:
                reasons.append("push state could not be checked")
            else:
                unpushed = int(ahead.stdout.strip()) > 0
                if unpushed:
                    reasons.append("worktree has unpushed commits")
    if record.phase != "complete":
        reasons.append(f"run is {record.phase}")
    return WorktreeInspection(
        run_id,
        path,
        record.branch,
        record.phase,
        registered,
        dirty,
        unpushed,
        not reasons,
        tuple(reasons),
    )


def list_worktrees(repo: Path) -> list[WorktreeInspection]:
    store = RunStore(repo)
    if not store.directory.is_dir():
        return []
    return [
        inspect_worktree(repo, path.stem)
        for path in sorted(store.directory.glob("*.json"))
    ]


def clean_worktree(repo: Path, run_id: str) -> WorktreeInspection:
    inspection = inspect_worktree(repo, run_id)
    if not inspection.safe_to_clean:
        raise WorktreeSafetyError("Refusing cleanup: " + "; ".join(inspection.reasons))
    result = _git(repo.resolve(), "worktree", "remove", str(inspection.path))
    if result.returncode:
        raise WorktreeSafetyError(result.stderr.strip() or "git worktree remove failed")
    return inspection
