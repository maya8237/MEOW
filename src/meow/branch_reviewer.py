"""
meow/branch_reviewer.py

Branch-name sanitizing for `review_cli.py`'s `--branch` review source
(`meow review --branch <branch> --target <target>`): the worktree
directory for a branch under review is named
`branch-review-<sanitized-branch>`, stripping anything that isn't a safe,
single path segment (slashes in `feature/x`-style branch names included).
"""

import re

_BRANCH_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(component: str) -> str:
    return _BRANCH_UNSAFE.sub("-", component).strip("-")
