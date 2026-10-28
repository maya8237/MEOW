"""Run user-defined hook commands (`hooks/*.toml` definitions).

A hook command gets the event's JSON on stdin, runs in the active directory,
and sees `MEOW_HOOK`, `MEOW_EVENT`, and `MEOW_ROLE`. Exit 0 continues. Exit 2
sends its stderr back to the agent (denying a `PreToolUse` call, blocking a
prompt or stop). Any other exit, a timeout, or a spawn failure is logged and
ignored. A hook can only restrict or inform: its output never grants a
permission, so gates and permissions stay code-owned.
"""

import asyncio
import json
import os
import re
import signal
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import HookMatcher

from meow.infrastructure.logging import get_logger
from meow.project.custom.definitions import CustomHook
from meow.project.custom.hook_files import SDK_EVENTS, TOOL_EVENTS

logger = get_logger(__name__)
BLOCK_EXIT_CODE = 2
# Set in every MEOW SDK agent so synced plugin hooks do not run a second time.
SDK_AGENT_ENV = "MEOW_SDK_AGENT"
_STDERR_LIMIT = 4000


@dataclass(frozen=True)
class HookResult:
    hook: str
    exit_code: int | None  # None: timed out or failed to start
    stderr: str = ""

    @property
    def blocking(self) -> bool:
        return self.exit_code == BLOCK_EXIT_CODE


def matching(hooks: Iterable[CustomHook], payload: dict) -> list[CustomHook]:
    """Hooks whose `matcher` accepts the payload's tool name."""
    tool = str(payload.get("tool_name", ""))
    return [
        hook
        for hook in hooks
        if hook.matcher is None or re.fullmatch(hook.matcher, tool)
    ]


def _kill(process: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError, PermissionError, OSError):
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)


async def run_hook(
    hook: CustomHook, payload: dict, cwd: Path, role: str | None = None
) -> HookResult:
    env = {
        **os.environ,
        "MEOW_HOOK": hook.name,
        "MEOW_EVENT": hook.event,
        "MEOW_ROLE": role or "",
    }
    try:
        process = await asyncio.create_subprocess_shell(
            hook.command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        data = json.dumps({**payload, "hook_event_name": hook.event}).encode()
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(data), timeout=hook.timeout
            )
        except TimeoutError:
            _kill(process)
            await process.wait()
            logger.warning("user_hook_timeout", hook=hook.name, timeout=hook.timeout)
            return HookResult(hook.name, None)
    except OSError as exc:
        logger.warning("user_hook_failed", hook=hook.name, reason=str(exc))
        return HookResult(hook.name, None)
    text = stderr.decode("utf-8", "replace").strip()[:_STDERR_LIMIT]
    if process.returncode not in {0, BLOCK_EXIT_CODE}:
        logger.warning(
            "user_hook_error", hook=hook.name, exit_code=process.returncode, stderr=text
        )
    return HookResult(hook.name, process.returncode, text)


def blocking_message(results: Iterable[HookResult]) -> str:
    """The agent-facing text of every hook that exited 2."""
    return "\n\n".join(
        f"Hook {result.hook}: {result.stderr or 'blocked'}"
        for result in results
        if result.blocking
    )


def feedback(event: str, message: str) -> dict:
    """The SDK hook output that delivers `message` for `event`."""
    if event == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }
    if event in TOOL_EVENTS:
        return {
            "hookSpecificOutput": {"hookEventName": event, "additionalContext": message}
        }
    return {"decision": "block", "reason": message}


def _callback(hooks: list[CustomHook], event: str, cwd: Path, role: str):
    async def run_user_hooks(input_data, tool_use_id, context):
        chosen = matching(hooks, input_data)
        results = [await run_hook(hook, input_data, cwd, role) for hook in chosen]
        message = blocking_message(results)
        return feedback(event, message) if message else {}

    return run_user_hooks


def sdk_hooks(
    hooks: Iterable[CustomHook], cwd: Path, role: str
) -> dict[str, list[HookMatcher]]:
    """SDK `hooks=` entries running `hooks` for `role`, grouped by event."""
    grouped: dict[str, list[CustomHook]] = {}
    for hook in hooks:
        if hook.event in SDK_EVENTS and hook.applies_to(role):
            grouped.setdefault(hook.event, []).append(hook)
    return {
        event: [HookMatcher(hooks=[_callback(chosen, event, cwd, role)])]
        for event, chosen in grouped.items()
    }


def merge_hooks(*tables: dict | None) -> dict:
    """Combine SDK `hooks=` tables, keeping every event's matchers in order."""
    merged: dict[str, list] = {}
    for table in tables:
        for event, matchers in (table or {}).items():
            merged.setdefault(event, []).extend(matchers)
    return merged


async def run_lifecycle(
    hooks: Iterable[CustomHook], event: str, payload: dict, cwd: Path
) -> None:
    """Run MEOW lifecycle hooks; they are advisory and never raise."""
    for hook in hooks:
        if hook.event == event:
            result = await run_hook(hook, {"event": event, **payload}, cwd)
            if result.blocking:
                logger.warning(
                    "user_hook_ignored_block", hook=hook.name, hook_event=event
                )
