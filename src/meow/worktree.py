"""Git/worktree bootstrapping for the harness's working directory.

Kept separate from `orchestrator.py`'s agent wiring and round-loop
orchestration: this module only ever touches the filesystem and `git`, and
has no dependency on `Sprint`, config, or any agent.
"""

import shutil
import subprocess
from pathlib import Path


def _ensure_gitignore_entry(working_dir: Path, entry: str = ".worktrees/") -> None:
    """Ensure the repo's .gitignore includes the given ignored path."""
    gitignore = working_dir / ".gitignore"
    contents = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = contents.splitlines()
    normalized = {line.strip() for line in lines}
    if entry in normalized or entry.rstrip("/") in normalized:
        return

    with gitignore.open("a", encoding="utf-8") as handle:
        if contents and not contents.endswith("\n"):
            handle.write("\n")
        handle.write(f"{entry}\n")


def _boot_repo(working_dir: Path, *, include_gitignore: bool = True) -> None:
    """Run working-directory boot checks every meow command needs."""
    if include_gitignore:
        _ensure_gitignore_entry(working_dir)


def _resolve_working_dir(
    working_dir: Path,
    *,
    use_worktree: bool,
    feature_name: str | None,
) -> tuple[Path, str | None, bool]:
    """Return the active directory and optional feature name."""
    if not use_worktree:
        return working_dir, feature_name, False
    if not feature_name:
        raise ValueError("feature_name is required when use_worktree=True")

    return _ensure_feature_worktree(working_dir, feature_name), feature_name, True


def _ensure_feature_worktree(working_dir: Path, feature_name: str) -> Path:
    """Create a per-feature worktree under the repo's .worktrees directory.

    Raises if `git worktree add` fails, rather than silently falling back to
    a plain, non-git directory -- a generator session pointed at such a
    directory would run against an empty project with no error ever
    surfaced.
    """
    worktrees_dir = working_dir / ".worktrees"
    worktree_dir = worktrees_dir / feature_name

    if worktree_dir.exists():
        return worktree_dir

    worktrees_dir.mkdir(parents=True, exist_ok=True)

    git = shutil.which("git")
    if not git:
        raise RuntimeError(
            "git is not installed or not on PATH; cannot create a feature "
            f"worktree at {worktree_dir}."
        )

    try:
        subprocess.run(
            [
                git,
                "-C",
                str(working_dir),
                "worktree",
                "add",
                "--detach",
                str(worktree_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to create worktree at {worktree_dir}: "
            f"{exc.stderr.strip() or exc}"
        ) from exc

    return worktree_dir


def _run_git(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _ensure_branch_worktree(
    working_dir: Path, feature_name: str, branch_name: str
) -> Path:
    """Create (or reuse) a worktree checked out on a real, pushable branch.

    Unlike `_ensure_feature_worktree` (detached, never pushed -- what `run`/
    `plan` need), `issue-solver` always needs a real branch to hand back to
    its caller, so it creates its own worktree on one instead.
    """
    worktree_dir = working_dir / ".worktrees" / feature_name
    if worktree_dir.exists():
        return worktree_dir

    git = shutil.which("git")
    if not git:
        raise RuntimeError(
            "git is required for issue-solver's worktree/branch/push steps."
        )

    (working_dir / ".worktrees").mkdir(parents=True, exist_ok=True)
    branch_exists = _run_git(
        [git, "rev-parse", "--verify", "--quiet", branch_name], cwd=working_dir
    ).returncode == 0

    if branch_exists:
        argv = [git, "worktree", "add", str(worktree_dir), branch_name]
    else:
        argv = [git, "worktree", "add", "-b", branch_name, str(worktree_dir)]
    result = _run_git(argv, cwd=working_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not create worktree for branch '{branch_name}':\n{result.stderr}"
        )
    return worktree_dir


def _push_branch(worktree_dir: Path, branch_name: str) -> None:
    """Push a finished issue-solver branch to `origin`."""
    git = shutil.which("git")
    remotes = _run_git([git, "remote"], cwd=worktree_dir).stdout.split()
    if "origin" not in remotes:
        raise RuntimeError(
            "No 'origin' remote configured -- issue-solver can't push the "
            f"finished branch '{branch_name}'. Add one with `git remote add "
            "origin <url>`, or push it yourself."
        )

    result = _run_git([git, "push", "-u", "origin", branch_name], cwd=worktree_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"git push failed for branch '{branch_name}':\n{result.stderr}"
        )
