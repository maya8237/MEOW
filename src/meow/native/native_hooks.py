"""`meow native hook`: run `hooks/*.toml` definitions for a Claude Code event.

Claude Code hooks (a plugin's `hooks.json`, a skill's `hooks:` frontmatter, or
`settings.json`) call this with the event payload on stdin, so the same
definitions work in any Claude session, not only inside MEOW's SDK agents.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from meow.infrastructure.user_hooks import (
    BLOCK_EXIT_CODE,
    SDK_AGENT_ENV,
    blocking_message,
    matching,
    run_hook,
)
from meow.project import merge_guard
from meow.project.config import config_root, load_config


def _payload() -> dict:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _run(hooks, payload: dict, active_dir: Path, role: str | None) -> list:
    return [await run_hook(hook, payload, active_dir, role) for hook in hooks]


@dataclass(frozen=True)
class HookOptions:
    """Which hooks `hook` runs beyond the event itself."""

    event: str
    role: str | None = None
    name: str | None = None  # just this definition (a synced plugin entry)


def hook(working_dir: Path, active_dir: Path, options: HookOptions) -> dict:
    """Run the event's hooks; exit 2 with their stderr when one blocks.

    Inside a MEOW SDK agent (`MEOW_SDK_AGENT`) this is a no-op: the agent
    already runs them.
    """
    event, role, name = options.event, options.role, options.name
    if os.environ.get(SDK_AGENT_ENV):
        return {}
    custom = load_config(config_root(working_dir, active_dir))["customizations"]
    chosen = [
        item
        for item in custom.hooks_for(event, role)
        if (name is None or item.name == name)
        and (role or not (item.roles or item.source.roles))
    ]
    payload = _payload()
    results = asyncio.run(_run(matching(chosen, payload), payload, active_dir, role))
    message = blocking_message(results)
    if message:
        print(message, file=sys.stderr)
        raise SystemExit(BLOCK_EXIT_CODE)
    return {}


def merge_guard_hook(working_dir: Path, active_dir: Path) -> dict:
    """`PreToolUse` hook of the run/autopilot/review skills: exit 2 (deny) on a
    PR/MR merge or a push to a protected branch, unless `[delivery]
    allow_merge` is set or the user's latest typed message asks to merge."""
    payload = _payload()
    tool_input = payload.get("tool_input")
    try:
        config = load_config(config_root(working_dir, active_dir))
    except (OSError, ValueError):
        config = {}
    why = merge_guard.reason(
        str(payload.get("tool_name") or ""),
        tool_input if isinstance(tool_input, dict) else {},
        merge_guard.protected_branches(config),
    )
    if why is None or merge_guard.merge_allowed(config):
        return {}
    transcript = payload.get("transcript_path")
    if merge_guard.asked_to_merge(
        merge_guard.latest_user_message(
            transcript if isinstance(transcript, str) else None
        )
    ):
        return {}
    print(why, file=sys.stderr)
    raise SystemExit(BLOCK_EXIT_CODE)
