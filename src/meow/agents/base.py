"""Shared setup and one-shot execution for MEOW agents."""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from meow.infrastructure.logging import get_logger
from meow.infrastructure.usage import record_result
from meow.project.config_models import LintCommand
from meow.project.permissions import (
    PATH_KEYS,
    PermissionPolicy,
    make_permission_callback,
)

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


def tool_input_path(tool_input: dict[str, Any], project_dir: Path) -> Path | None:
    """The resolved file a tool call targets, relative paths from `project_dir`."""
    raw = next((tool_input[key] for key in PATH_KEYS if key in tool_input), None)
    if not isinstance(raw, str) or not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        return candidate.resolve()
    except (OSError, RuntimeError):
        return None


def guard_tools(
    options: ClaudeAgentOptions,
    tools: set[str],
    deny_reason: Callable[[dict[str, Any]], str | None],
    *,
    interrupt: bool = False,
) -> None:
    """Route `tools` through `deny_reason` before any earlier callback.

    The SDK auto-approves whole-tool `allowed_tools` entries without calling
    `can_use_tool`, so guarded tools are dropped from that list; they stay
    available and fall through to the callback instead.
    """
    options.allowed_tools = [t for t in options.allowed_tools if t not in tools]
    previous = options.can_use_tool

    async def can_use_tool(tool: str, tool_input: dict[str, Any], context: object):
        if tool in tools and (reason := deny_reason(tool_input)):
            return PermissionResultDeny(message=reason, interrupt=interrupt)
        if previous is not None:
            return await previous(tool, tool_input, context)
        return PermissionResultAllow()

    options.can_use_tool = can_use_tool


def restrict_writes(
    options: ClaudeAgentOptions, output_file: Path, project_dir: Path
) -> None:
    """Let a fetcher write only its own output file."""
    target = output_file.resolve()
    guard_tools(
        options,
        {"Write"},
        lambda tool_input: None
        if tool_input_path(tool_input, project_dir) == target
        else f"Only the output file {target} may be written",
        interrupt=True,
    )


def prepare_output_file(output_file: Path) -> None:
    """Remove a prior output so a later existence check cannot accept stale data."""
    output_file.unlink(missing_ok=True)


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


def _message_session_id(message: object) -> str | None:
    if isinstance(message, ResultMessage):
        return message.session_id or None
    if isinstance(message, SystemMessage):
        session_id = message.data.get("session_id")
        return session_id if isinstance(session_id, str) and session_id else None
    return None


def _remember_session(options: object, message: object) -> None:
    callback = getattr(options, "_meow_session_callback", None)
    session_id = _message_session_id(message)
    if callable(callback) and session_id:
        callback(session_id)


def log_stream_message(  # ruff: ignore[complex-structure]
    role: str, message: object, *, options: ClaudeAgentOptions | object | None = None
) -> None:
    """Log one SDK message as it streams in, so a long-running turn stays
    visibly alive instead of going silent until the final result."""
    if options is not None:
        _remember_session(options, message)
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


def _last_text_block(message: AssistantMessage) -> str:
    """The text of the final `TextBlock` in an assistant message, or ""."""
    texts = [b.text for b in message.content if isinstance(b, TextBlock)]
    return texts[-1] if texts else ""


async def _consume_query(prompt: str, options: ClaudeAgentOptions, role: str) -> None:
    """Run `query()` once to completion, raising `RuntimeError` on a
    non-success result. A `ProcessError` (the CLI subprocess crashed or
    otherwise failed) propagates unchanged for `Agent.run_query`'s retry
    loop to handle."""
    messages: AsyncIterator = query(prompt=prompt, options=options)
    last_text = ""
    try:
        async for message in messages:
            log_stream_message(role, message, options=options)
            if isinstance(message, AssistantMessage):
                last_text = _last_text_block(message) or last_text
            if isinstance(message, ResultMessage) and message.subtype != "success":
                logger.error("agent_query_failed", role=role, subtype=message.subtype)
                raise RuntimeError(f"{role} failed: {message.subtype}")
    except ProcessError as exc:
        if not exc.stderr and last_text:
            exc.stderr = last_text
        raise


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

    def skills(self, role: str, built_in: list[str] | None = None) -> list[str]:
        """Combine project defaults, role skills, and built-ins once."""
        configured = getattr(self.context, "config", {}).get("agent_skills", {})
        values = []
        if isinstance(configured, dict):
            for key in ("default", role.lower()):
                entries = configured.get(key, [])
                if isinstance(entries, list):
                    values.extend(
                        entry for entry in entries if isinstance(entry, str) and entry
                    )
        values.extend(entry for entry in built_in or [] if entry)
        return list(dict.fromkeys(values))

    def _apply_policy(
        self, role: str, allowed_tools: list[str], extra_options: dict
    ) -> list[str]:
        """Attach the role's permission callback; return the narrowed tools."""
        policy = self.context.config.get("permissions")
        if not isinstance(policy, PermissionPolicy):
            return allowed_tools
        role_policy = policy.for_role(role)
        if not role_policy.rules:
            return allowed_tools
        extra_options["can_use_tool"] = make_permission_callback(
            role_policy,
            unattended=bool(self.context.config.get("_unattended", False)),
            project_root=self.context.active_working_dir(),
        )
        # Auto-approved tools would never reach the callback; under dontAsk
        # nothing does, so stripping them would only deny them.
        if extra_options.get("permission_mode") == "dontAsk":
            return allowed_tools
        gated = role_policy.gated_tools()
        return [tool for tool in allowed_tools if tool not in gated]

    def options(
        self,
        *,
        system_prompt: str,
        allowed_tools: list[str],
        role: str,
        **extra_options,
    ) -> ClaudeAgentOptions:
        """Build SDK options from project context and agent-specific values."""
        extra_options["skills"] = self.skills(role, extra_options.get("skills", []))
        allowed_tools = self._apply_policy(role, allowed_tools, extra_options)
        journal = self.context.config.get("_run_journal")
        if journal is not None:
            store, run_id = journal
            record = store.load(run_id)
            role_key = role.lower()
            session_id = record.sessions.get(role_key)
            # Only `meow resume` continues a saved session, and only for the
            # role's first query: later rounds (e.g. each review) start fresh.
            if role_key in self.context.config.get("_resume_required_roles", ()):
                if not session_id:
                    raise RuntimeError(
                        f"Cannot resume: {role_key} session is missing from run "
                        f"{run_id}."
                    )
                resumed = self.context.config.setdefault("_resumed_roles", set())
                if role_key not in resumed and "resume" not in extra_options:
                    extra_options["resume"] = session_id
                    resumed.add(role_key)
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model=self.context.model(role),
            cwd=str(self.context.active_working_dir()),
            **extra_options,
        )
        if journal is not None:
            store, run_id = journal
            options._meow_session_callback = lambda session_id: store.set_session(
                run_id, role.lower(), session_id
            )
        return options

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


class SessionAgent(Agent):
    """A role that keeps one `ClaudeSDKClient` session across rounds, so later
    turns remember what earlier ones already tried."""

    role = ""

    def _open_session(self, options: ClaudeAgentOptions) -> None:
        self._session_options = options
        self._client = ClaudeSDKClient(options=options)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def _turn(self, message: str) -> str:
        """Send one message and return the assistant's text for that turn."""
        logger.info("session_turn_started", role=self.role)
        await self._client.query(message)
        text = []
        async for item in self._client.receive_response():
            log_stream_message(self.role, item, options=self._session_options)
            if isinstance(item, AssistantMessage):
                text.extend(
                    block.text for block in item.content if isinstance(block, TextBlock)
                )
        logger.info("session_turn_finished", role=self.role)
        return "\n".join(text)


class ConfigLookups:
    """Model and lint lookups every `AgentContext` derives from `config`."""

    config: dict

    def model(self, role: str) -> str | None:
        models = self.config["models"]
        return models.get(role, models.get("reviewer"))

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]


class ProjectContext(ConfigLookups):
    """Config- and directory-only `AgentContext` for non-sprint flows.

    Built from project configuration and a directory alone, with no sprint
    object, plan file, or contract dependency -- used by review operations
    that don't loop a generator against a Sprint Contract, such as `cr`.
    """

    def __init__(self, repo_dir: Path, config: dict, *, use_worktree: bool = False):
        self.repo_dir = repo_dir
        self.config = config
        self.use_worktree = use_worktree

    def active_working_dir(self) -> Path:
        return self.repo_dir
