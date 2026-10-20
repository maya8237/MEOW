"""Readable result names, with collision checks under the worktree lock."""

import re
from pathlib import Path

from meow.infrastructure.worktree import (
    _reject_reserved_name,
    _run_git,
    _serialize_worktree_creation,
)


def request_name(request: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", request.lower()).strip("-")[:60].rstrip("-")
    name = name or "feature"
    try:
        _reject_reserved_name(name)
    except RuntimeError:
        name = f"feature-{name}"
    return name


@_serialize_worktree_creation
def result_branch(  # ruff: ignore[complex-structure, too-many-statements]
    directory: Path, name: str, *, old: str | None = None
) -> str:
    """Name a detached result, or rename an explicitly owned legacy branch."""
    checked_out = _run_git(["git", "branch", "--show-current"], cwd=directory)
    if checked_out.returncode:
        raise RuntimeError(
            checked_out.stderr.strip() or "Cannot inspect current branch"
        )
    current = checked_out.stdout.strip()
    if current and current != old:
        return current
    refs = _run_git(
        ["git", "for-each-ref", "--format=%(refname)", "refs/heads/"], cwd=directory
    )
    if refs.returncode:
        raise RuntimeError(refs.stderr.strip() or "Cannot inspect result branches")
    branches = set(refs.stdout.splitlines())
    prefix = "meow-" if "refs/heads/meow" in branches else "meow/"
    stem = f"{prefix}{request_name(name)}"
    for index in range(1, 1001):
        branch = stem if index == 1 else f"{stem}-{index}"
        ref = f"refs/heads/{branch}"
        if ref in branches or any(item.startswith(f"{ref}/") for item in branches):
            continue
        argv = (
            ["git", "branch", "-m", branch]
            if current == old
            else [
                "git",
                "switch",
                "-c",
                branch,
            ]
        )
        result = _run_git(argv, cwd=directory)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Cannot name result branch")
        return branch
    raise RuntimeError(f"No free result branch for {name!r}")
