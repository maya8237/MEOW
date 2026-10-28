"""`meow native hook`: run `hooks/*.toml` definitions for a Claude Code event.

Claude Code hooks (a plugin's `hooks.json`, a skill's `hooks:` frontmatter, or
`settings.json`) call this with the event payload on stdin, so the same
definitions work in any Claude session, not only inside MEOW's SDK agents.
"""

import asyncio
import json
import sys
from pathlib import Path

from meow.infrastructure.user_hooks import (
    BLOCK_EXIT_CODE,
    blocking_message,
    matching,
    run_hook,
)
from meow.project.config import config_root, load_config


def _payload() -> dict:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _run(hooks, payload: dict, active_dir: Path, role: str | None) -> list:
    return [await run_hook(hook, payload, active_dir, role) for hook in hooks]


def hook(working_dir: Path, active_dir: Path, event: str, role: str | None) -> dict:
    """Run the event's hooks; exit 2 with their stderr when one blocks."""
    custom = load_config(config_root(working_dir, active_dir))["customizations"]
    chosen = [
        item
        for item in custom.hooks_for(event, role)
        if role or not (item.roles or item.source.roles)
    ]
    payload = _payload()
    results = asyncio.run(_run(matching(chosen, payload), payload, active_dir, role))
    message = blocking_message(results)
    if message:
        print(message, file=sys.stderr)
        raise SystemExit(BLOCK_EXIT_CODE)
    return {}
