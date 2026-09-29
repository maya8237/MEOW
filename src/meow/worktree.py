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
