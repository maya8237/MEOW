"""What changed in the working tree between two reviews.

`code_revision` is a content hash and cannot be diffed, so each review also
records a git tree object of the working tree (tracked and untracked, ignored
files left out) built through a throwaway index. The real index and the
working tree are never touched.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TreeDelta:
    stat: str
    diff: str
    since: str  # "previous review" or "HEAD"


def _git(repo: Path, *argv: str, env: dict | None = None) -> str | None:
    try:
        done = subprocess.run(
            ["git", *argv],
            cwd=repo,
            capture_output=True,
            check=False,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _pathspecs(repo: Path, exclude: tuple[Path, ...]) -> list[str]:
    specs = [":(exclude).meow"]
    for path in exclude:
        try:
            specs.append(f":(exclude){path.resolve().relative_to(repo.resolve())}")
        except ValueError:
            continue
    return specs


def snapshot_tree(repo: Path, exclude: tuple[Path, ...] = ()) -> str | None:
    """Tree object id of the working tree, or None outside a usable repository."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        has_head = _git(repo, "rev-parse", "--verify", "-q", "HEAD") is not None
        if has_head and _git(repo, "read-tree", "HEAD", env=env) is None:
            return None
        specs = _pathspecs(repo, exclude)
        if _git(repo, "add", "-A", "--", ".", *specs, env=env) is None:
            return None
        tree = _git(repo, "write-tree", env=env)
    return tree.strip() if tree else None


def changes_since(
    repo: Path, tree: str | None, exclude: tuple[Path, ...] = ()
) -> TreeDelta:
    """Stat and diff of the working tree against `tree`, else against HEAD."""
    current = snapshot_tree(repo, exclude) if tree else None
    if tree and current:
        stat = _git(repo, "diff", "--stat", tree, current)
        diff = _git(repo, "diff", tree, current)
        if stat is not None and diff is not None:
            return TreeDelta(stat.strip(), diff, "previous review")
    return TreeDelta(
        (_git(repo, "diff", "--stat", "HEAD") or "").strip(),
        _git(repo, "diff", "HEAD") or "",
        "HEAD",
    )
