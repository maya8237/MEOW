"""
meow/native.py

The deterministic half of native (in-Claude-Code-session) execution.

When a meow skill runs inside a Claude Code session, that session plays
planner/generator and dispatches reviewer/explorer subagents itself -- no
Agent SDK process is involved. What the session cannot do reliably from
prose alone are the mechanical facts the Python harness already owns:
parsed `.harness.toml`, worktree and branch resolution, plan/review
lookup, lint execution, verdict parsing, the round counter, and the role
prompts. `native_cli.py` wires the functions re-exported here to `meow
native ...` and prints their results as JSON.

Each concern that used to live in this one file now has its own module:
`native_prepare.py` (worktree/clean-tree bootstrapping, directory
resolution, and plan/review lookup), `native_lint.py` (lint execution),
`native_state.py` (the on-disk round counter), and `native_prompt.py`
(prompt/agent-wiring construction). This module just re-exports their
public names, plus the one-line git-push wrapper, so `native_cli.py` and
other callers keep a single `from meow import native` import and a stable
`native.<name>` surface. Nothing here imports or starts the Agent SDK.
"""

from pathlib import Path

from meow.native_lint import LintOptions, lint
from meow.native_prepare import (
    PrepareOptions,
    latest_plan,
    latest_review,
    prepare,
    verdict,
)
from meow.native_prompt import PROMPT_ROLES, role_prompt
from meow.native_state import round_state
from meow.worktree import _push_branch

__all__ = [
    "PROMPT_ROLES",
    "LintOptions",
    "PrepareOptions",
    "latest_plan",
    "latest_review",
    "lint",
    "prepare",
    "push",
    "role_prompt",
    "round_state",
    "verdict",
]


def push(active_dir: Path, branch: str) -> dict:
    _push_branch(active_dir, branch)
    return {"branch": branch, "pushed": True}
