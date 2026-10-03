"""Shared setup and one-shot execution for MEOW agents."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ProcessError,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from meow.config import LintCommand
from meow.logging import get_logger
from meow.permissions import PermissionPolicy, make_permission_callback
from meow.usage import record_result

logger = get_logger(__name__)

_PREVIEW_LEN = 200
_SDK_CRASH_RETRY_ATTEMPTS = 3
_SDK_CRASH_RETRY_BACKOFF = 1.0
_NTSTATUS_SEVERITY_BIT = 0x80000000


def _looks_like_a_crash(exit_code: int | None) -> bool:
    """True if `exit_code` looks like the process crashed or was killed
    rather than exiting normally with a chosen status: negative (POSIX:
    killed by signal, e.g. -11 for SIGSEGV; Windows: subprocess/asyncio
    commonly sign-extends a fast-fail NTSTATUS code like 0xC0000409 into a
    large negative number), or, if reported as the raw unsigned NTSTATUS
    value instead, a number with the top bit of a 32-bit word set. A
    well-behaved CLI never returns a negative or huge-unsigned exit code,
    so this reliably separates "crashed" from "exited with a chosen
    nonzero status" on both platforms without OS-specific branching."""
    if exit_code is None:
        return False
    return exit_code < 0 or exit_code >= _NTSTATUS_SEVERITY_BIT


def _preview(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= _PREVIEW_LEN else text[:_PREVIEW_LEN] + "..."


def _log_assistant_block(role: str, block: object) -> None:
    if isinstance(block, TextBlock):
        logger.info("agent_text", role=role, text=_preview(block.text))
    elif isinstance(block, ToolUseBlock):
        logger.info("agent_tool_use", role=role, tool=block.name)
    elif isinstance(block, ThinkingBlock):
        logger.debug("agent_thinking", role=role, text=_preview(block.thinking))


def _log_user_block(role: str, block: object) -> None:
    if isinstance(block, ToolResultBlock):
        logger.debug("agent_tool_result", role=role, is_error=bool(block.is_error))


def log_stream_message(role: str, message: object) -> None:
    """Log one SDK message as it streams in, so a long-running turn stays
    visibly alive instead of going silent until the final result."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            _log_assistant_block(role, block)
    elif isinstance(message, UserMessage):
        for block in message.content if isinstance(message.content, list) else []:
            _log_user_block(role, block)
    elif isinstance(message, ResultMessage):
        record_result(role, message)
        logger.info(
            "agent_result",
            role=role,
            subtype=message.subtype,
            num_turns=message.num_turns,
            duration_ms=message.duration_ms,
        )


class AgentContext(Protocol):
    """Project configuration every agent role needs, independent of sprint
    lifecycle -- what `Agent.options()` reads, nothing role-specific."""

    @property
    def config(self) -> dict: ...

    @property
    def repo_dir(self) -> Path: ...

    @property
    def use_worktree(self) -> bool: ...

    def model(self, role: str) -> str | None: ...

    def lint_commands(self) -> list[LintCommand]: ...

    def active_working_dir(self) -> Path: ...


class GeneratorContext(AgentContext, Protocol):
    """`AgentContext` plus the pre-wired explorer and lint hook only the
    generator role needs (it hands both to the SDK directly)."""

    @property
    def explorer(self) -> AgentDefinition: ...

    @property
    def lint_hook(self) -> object: ...


async def _consume_query(prompt: str, options: ClaudeAgentOptions, role: str) -> None:
    """Run `query()` once to completion, raising `RuntimeError` on a
    non-success result. A `ProcessError` (the CLI subprocess crashed or
    otherwise failed) propagates unchanged for `Agent.run_query`'s retry
    loop to handle."""
    messages: AsyncIterator = query(prompt=prompt, options=options)
    async for message in messages:
        log_stream_message(role, message)
        if isinstance(message, ResultMessage) and message.subtype != "success":
            logger.error("agent_query_failed", role=role, subtype=message.subtype)
            raise RuntimeError(f"{role} failed: {message.subtype}")


def _process_error_message(
    role: str, exc: ProcessError, *, crashed: bool, attempt: int
) -> str:
    stderr_text = exc.stderr or "no stderr captured"
    if crashed:
        return (
            f"{role}'s `claude` CLI subprocess crashed (exit code "
            f"{exc.exit_code}) after {attempt} attempt(s): {stderr_text}"
        )
    return (
        f"{role}'s `claude` CLI subprocess failed (exit code "
        f"{exc.exit_code}): {stderr_text}"
    )


class Agent:
    """Common setup for agent roles using a generic project context."""

    def __init__(self, context: AgentContext):
        self.context = context

    def options(
        self,
        *,
        system_prompt: str,
        allowed_tools: list[str],
        role: str,
        **extra_options,
    ) -> ClaudeAgentOptions:
        """Build SDK options from project context and agent-specific values."""
        policy = self.context.config.get("permissions")
        if isinstance(policy, PermissionPolicy):
            role_policy = policy.for_role(role)
            if role_policy.rules:
                extra_options["can_use_tool"] = make_permission_callback(
                    role_policy,
                    unattended=bool(self.context.config.get("_unattended", False)),
                    project_root=self.context.active_working_dir(),
                )
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model=self.context.model(role),
            cwd=str(self.context.active_working_dir()),
            **extra_options,
        )

    @staticmethod
    async def run_query(prompt: str, options: ClaudeAgentOptions, role: str) -> None:
        """Run a one-shot SDK query and raise when the SDK reports failure.

        Retries up to `_SDK_CRASH_RETRY_ATTEMPTS` times, with a short
        exponential backoff, when the underlying `claude` CLI subprocess
        crashes -- a `ProcessError` whose exit code looks like an abnormal
        termination (see `_looks_like_a_crash`) rather than a normal,
        chosen nonzero exit. `query()` is documented as stateless/one-shot
        (no conversation carried over), so retrying by starting a fresh
        query is safe: nothing meow-level has mutated when a crash happens
        mid-query, and a successful retry re-runs the same prompt/options
        from scratch. A non-crash `ProcessError` (the CLI itself exiting
        with a real error) is never retried, but is still wrapped in a
        clear, role-aware message instead of the SDK's own generic one. The
        existing non-success `ResultMessage` path (a graceful SDK-reported
        failure, e.g. max turns reached) is untouched -- that's not a
        crash, and retrying it wouldn't change the outcome.
        """
        delay = _SDK_CRASH_RETRY_BACKOFF
        for attempt in range(1, _SDK_CRASH_RETRY_ATTEMPTS + 1):
            logger.info("agent_query_started", role=role, attempt=attempt)
            try:
                await _consume_query(prompt, options, role)
                logger.info("agent_query_finished", role=role)
                return
            except ProcessError as exc:
                crashed = _looks_like_a_crash(exc.exit_code)
                if crashed and attempt < _SDK_CRASH_RETRY_ATTEMPTS:
                    logger.warning(
                        "agent_query_crash_retry",
                        role=role,
                        attempt=attempt,
                        exit_code=exc.exit_code,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                logger.error(
                    "agent_query_crashed" if crashed else "agent_query_process_error",
                    role=role,
                    attempt=attempt,
                    exit_code=exc.exit_code,
                    stderr=exc.stderr,
                )
                raise RuntimeError(
                    _process_error_message(role, exc, crashed=crashed, attempt=attempt)
                ) from exc


class ProjectContext:
    """Config- and directory-only `AgentContext` for non-sprint flows.

    Built from project configuration and a directory alone, with no sprint
    object, plan file, or contract dependency -- used by review operations
    that don't loop a generator against a Sprint Contract, such as `cr`.
    """

    def __init__(self, repo_dir: Path, config: dict, *, use_worktree: bool = False):
        self.repo_dir = repo_dir
        self.config = config
        self.use_worktree = use_worktree

    def model(self, role: str) -> str | None:
        models = self.config["models"]
        return models.get(role, models.get("reviewer"))

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]

    def active_working_dir(self) -> Path:
        return self.repo_dir
