"""Shared setup and one-shot execution for MEOW agents."""

import asyncio
import copy
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    CLINotFoundError,
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
from meow.project.custom import CustomAgent, Customizations, skill_plugin_dir
from meow.project.custom.rendering import builtin_agent, render_agent
from meow.project.permissions import (
    PATH_KEYS,
    PermissionPolicy,
    make_permission_callback,
)

logger = get_logger(__name__)

_PREVIEW_LEN = 200
_BASH_TIMEOUT_ENV = {
    "BASH_DEFAULT_TIMEOUT_MS": str(30 * 60 * 1000),
    "BASH_MAX_TIMEOUT_MS": str(2 * 60 * 60 * 1000),
}
_SDK_CRASH_RETRY_ATTEMPTS = 3
_SDK_CRASH_RETRY_BACKOFF = 1.0
_NTSTATUS_SEVERITY_BIT = 0x80000000
_OUTPUT_UNLINK_ATTEMPTS = 3
_OUTPUT_UNLINK_BACKOFF = 0.2


def _is_os_failure(exc: BaseException) -> bool:
    """True for an operating-system failure (a `WinError`, a locked or
    denied file, a process that could not start), which a short wait and a
    fresh attempt often clears: a plain `OSError`, or an SDK connection error
    other than a missing CLI. Everything else is a real failure."""
    if isinstance(exc, CLINotFoundError):
        return False
    return isinstance(exc, OSError | CLIConnectionError)


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
        if tool in tools:
            try:
                reason = deny_reason(tool_input)
            except OSError as exc:  # cannot verify the target: refuse, don't crash
                reason = f"Could not check this call ({exc}); try another approach"
            if reason:
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
        lambda tool_input: (
            None
            if tool_input_path(tool_input, project_dir) == target
            else f"Only the output file {target} may be written"
        ),
        interrupt=True,
    )


def allow_only_tools(
    options: ClaudeAgentOptions, permitted: Callable[[str], bool], reason: str
) -> None:
    """Deny every tool call `permitted` rejects, before any earlier callback.

    `guard_tools` only gates the tools it names and lets the rest through,
    which does not help when tool names differ per MCP server. Here the
    default is deny: wildcard `allowed_tools` entries (which the SDK
    auto-approves without calling `can_use_tool`) are dropped so the callback
    sees every call.
    """
    options.allowed_tools = [t for t in options.allowed_tools if "*" not in t]
    previous = options.can_use_tool

    async def can_use_tool(tool: str, tool_input: dict[str, Any], context: object):
        if not permitted(tool):
            return PermissionResultDeny(message=reason)
        if previous is not None:
            return await previous(tool, tool_input, context)
        return PermissionResultAllow()

    options.can_use_tool = can_use_tool


def prepare_output_file(output_file: Path) -> None:
    """Remove a prior output so a later existence check cannot accept stale data.

    A file another process briefly holds open (a `WinError` sharing violation)
    is retried; if it stays locked it is emptied instead, and failing that the
    run continues with a warning rather than crashing before the agent starts.
    """
    for attempt in range(_OUTPUT_UNLINK_ATTEMPTS):
        try:
            output_file.unlink(missing_ok=True)
            return
        except OSError as exc:
            reason = str(exc)
            time.sleep(_OUTPUT_UNLINK_BACKOFF * (attempt + 1))
    try:
        output_file.write_text("", encoding="utf-8")
    except OSError:
        logger.warning("output_not_cleared", file=str(output_file), reason=reason)


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
        try:
            callback(session_id)
        except Exception as exc:  # a journal write must not abort the turn
            logger.warning("session_not_recorded", reason=str(exc))


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

    def model(self, role: str, agent: CustomAgent | None = None) -> str | None: ...

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


async def _consume_query(  # ruff: ignore[complex-structure]
    prompt: str, options: ClaudeAgentOptions, role: str
) -> str:
    """Run `query()` once to completion, raising `RuntimeError` on a
    non-success result. A `ProcessError` (the CLI subprocess crashed or
    otherwise failed) propagates unchanged for `Agent.run_query`'s retry
    loop to handle."""
    messages: AsyncIterator = query(prompt=prompt, options=options)
    last_text = ""
    parts = []
    try:
        async for message in messages:
            log_stream_message(role, message, options=options)
            if isinstance(message, AssistantMessage):
                last_text = _last_text_block(message) or last_text
                parts.extend(
                    block.text
                    for block in message.content
                    if isinstance(block, TextBlock)
                )
            if isinstance(message, ResultMessage):
                last_text = message.result or last_text
                if message.is_error or message.subtype != "success":
                    logger.error(
                        "agent_query_failed", role=role, subtype=message.subtype
                    )
                    raise RuntimeError(
                        f"{role} failed: {message.subtype}"
                        + (f": {last_text}" if last_text else "")
                    )
    except ProcessError as exc:
        if not exc.stderr and last_text:
            exc.stderr = last_text
        raise
    return "\n".join(parts)


def _check_os_retry(exc: BaseException, role: str, attempt: int) -> None:
    """Re-raise `exc` unless another attempt is worthwhile; else log the retry."""
    if not _is_os_failure(exc) or attempt >= _SDK_CRASH_RETRY_ATTEMPTS:
        raise exc
    logger.warning("os_error_retry", role=role, attempt=attempt, reason=str(exc))


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
        values.extend(self.customizations().skills_for(role.lower()))
        values.extend(entry for entry in built_in or [] if entry)
        return list(dict.fromkeys(values))

    def _apply_skills(self, role: str, extra_options: dict) -> None:
        """Resolve the role's skills and load the custom-skill plugin if any."""
        extra_options["skills"] = self.skills(role, extra_options.get("skills", []))
        plugin = skill_plugin_dir(self.customizations().skills)
        if plugin is not None:
            extra_options["plugins"] = [
                *extra_options.get("plugins", []),
                {"type": "local", "path": str(plugin)},
            ]

    def customizations(self) -> Customizations:
        custom = getattr(self.context, "config", {}).get("customizations")
        return custom if isinstance(custom, Customizations) else Customizations()

    def definition(self, name: str) -> CustomAgent:
        custom = self.customizations()
        if custom.catalog:
            return custom.agent(name)
        return builtin_agent(name)

    def delegates(
        self, agent: CustomAgent, parent_tools: list[str]
    ) -> dict[str, AgentDefinition]:
        definitions = self.custom_agents(agent.role, parent_tools)
        ceiling = [tool for tool in parent_tools if tool != "Agent"]
        for name in agent.agents:
            try:
                child = Agent.definition(self, name)
            except (KeyError, OSError) as exc:
                raise ValueError(f"{agent.file}: unknown delegate {name!r}") from exc
            definitions[name] = AgentDefinition(
                description=" ".join(child.description.split()),
                prompt=render_agent(
                    child, {"active_dir": self.context.active_working_dir()}
                ),
                tools=[
                    tool
                    for tool in (child.tools if child.tools is not None else ceiling)
                    if tool in ceiling
                ],
                model=self.context.model(child.role, child),
                skills=self.skills(child.role, list(child.skills)),
            )
        return definitions

    def definition_options(
        self, agent: CustomAgent, *, system_prompt: str, **extra
    ) -> ClaudeAgentOptions:
        tools = list(agent.tools or ())
        if "allowed_tools" in extra:
            tools = extra.pop("allowed_tools")
        extra.setdefault("tools", tools)
        extra.setdefault("agents", self.delegates(agent, tools))
        extra.setdefault("skills", list(agent.skills))
        return self.options(
            system_prompt=system_prompt,
            allowed_tools=tools,
            role=agent.role,
            definition=agent,
            **extra,
        )

    def custom_agents(
        self, role: str, parent_tools: list[str]
    ) -> dict[str, AgentDefinition]:
        """The role's custom subagents, each capped to the parent's tools.

        A subagent never gains a tool its parent role lacks, and never the
        Agent tool itself, so delegation cannot widen a role's access; the
        parent's permission policy still decides every call.
        """
        ceiling = [tool for tool in parent_tools if tool != "Agent"]
        definitions = {}
        for agent in self.customizations().agents_for(role.lower()):
            tools = ceiling
            if agent.tools is not None:
                tools = [tool for tool in agent.tools if tool in ceiling]
                dropped = sorted(set(agent.tools) - set(tools))
                if dropped:
                    logger.warning(
                        "custom_agent_tools_dropped",
                        agent=agent.name,
                        role=role,
                        tools=dropped,
                    )
            definitions[agent.name] = AgentDefinition(
                description=agent.description,
                prompt=agent.prompt,
                tools=tools,
                model=agent.model,
                skills=list(agent.skills),
            )
        return definitions

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
            auto=bool(self.context.config.get("_auto", False)),
            project_root=self.context.active_working_dir(),
        )
        # dontAsk pre-approves tools without consulting the callback, so the
        # policy must decide every call under the default mode instead.
        if extra_options.get("permission_mode") == "dontAsk":
            extra_options["permission_mode"] = "default"
        gated = role_policy.gated_tools()
        # Wildcard approvals also bypass the callback for explicitly gated tools.
        return [tool for tool in allowed_tools if tool not in gated and "*" not in tool]

    def options(  # ruff: ignore[complex-structure, too-many-statements]
        self,
        *,
        system_prompt: str,
        allowed_tools: list[str],
        role: str,
        definition: CustomAgent | None = None,
        **extra_options,
    ) -> ClaudeAgentOptions:
        """Build SDK options from project context and agent-specific values."""
        self._apply_skills(role, extra_options)
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
        # Long builds and test suites must not be killed by the CLI's default
        # two-minute Bash timeout; explicit user/env settings still win.
        env = dict(extra_options.pop("env", None) or {})
        for name, value in _BASH_TIMEOUT_ENV.items():
            env.setdefault(name, os.environ.get(name, value))
        options = ClaudeAgentOptions(
            env=env,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model=(
                self.context.model(role, definition)
                if definition is not None
                else self.context.model(role)
            ),
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
    async def run_query(prompt: str, options: ClaudeAgentOptions, role: str) -> str:
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
                response = await _consume_query(prompt, options, role)
                logger.info("agent_query_finished", role=role)
                return response
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
            except (OSError, CLIConnectionError) as exc:
                _check_os_retry(exc, role, attempt)
                await asyncio.sleep(delay)
                delay *= 2

    async def run_definition_query(self, agent, prompt, options, role):
        pool = self.context.config.get("_workflow_pool")
        if agent.session == "persistent" and pool is not None:
            return await pool.run(
                self.context,
                agent.role,
                options,
                f"{options.system_prompt}\n\n{prompt}",
            )
        return await self.run_query(prompt, options, role)


# A persistent SDK session grows with every turn. Past roughly this many input
# tokens the CLI starts failing every turn, so the session is rebuilt first.
_SESSION_TOKEN_LIMIT = 150_000
_TURN_ATTEMPTS = (1, 2)
_CONTEXT_OVERFLOW = re.compile(
    r"prompt is too long|context (?:window|length|limit)|too many tokens|"
    r"maximum context|exceed(?:s|ed)? .{0,30}context",
    re.I,
)


def _context_tokens(result: object) -> int:
    usage = getattr(result, "usage", None)
    if not isinstance(usage, dict):
        return 0
    return sum(
        value
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        if isinstance(value := usage.get(key), int) and not isinstance(value, bool)
    )


class SessionAgent(Agent):
    """A role that keeps one `ClaudeSDKClient` session across rounds, so later
    turns remember what earlier ones already tried.

    When the session nears the context limit (or a turn fails because it hit
    it), the client is rebuilt and the turn is replayed with a recap, so a long
    automatic run never dies on a full context window."""

    role = ""
    _entered = False
    _context_size = 0
    _recap = ""

    def _open_session(self, options: ClaudeAgentOptions) -> None:
        self._session_options = options
        self._client = ClaudeSDKClient(options=options)

    def open(self, options: ClaudeAgentOptions) -> None:
        """Prepare the session for callers outside the agent hierarchy."""
        self._open_session(options)

    async def send(self, message: str) -> str:
        """One turn for callers outside the agent hierarchy."""
        return await self._turn(message)

    async def __aenter__(self):
        await self._client.__aenter__()
        self._entered = True
        return self

    async def __aexit__(self, *exc):
        self._entered = False
        try:
            await self._client.__aexit__(*exc)
        except Exception as error:  # the process may already be gone
            logger.warning("session_close_failed", role=self.role, reason=str(error))

    async def _restart(self, reason: str = "reached its context limit") -> None:
        """Replace the full session with a fresh one carrying a short recap."""
        logger.warning(
            "session_recycled", role=self.role, context_tokens=self._context_size
        )
        old = self._client
        options = copy.copy(self._session_options)
        for name in ("resume", "continue_conversation"):
            if hasattr(options, name):
                setattr(options, name, None if name == "resume" else False)
        callback = getattr(self._session_options, "_meow_session_callback", None)
        if callback is not None:
            options._meow_session_callback = callback
        self._session_options = options
        self._client = ClaudeSDKClient(options=options)
        if self._entered:
            with suppress(Exception):
                await old.__aexit__(None, None, None)
            await self._client.__aenter__()
        self._context_size = 0
        self._recap = (
            f"Your previous session {reason} and was replaced. "
            "Re-read the plan and inspect the working tree (git status, git diff) "
            "to see what is already done; do not redo finished work.\n\n"
        )

    async def _turn(  # ruff: ignore[too-many-statements]
        self, message: str
    ) -> str:
        """Send one message and return the assistant's text for that turn.

        A full context window or a crashed CLI process replaces the session and
        replays the turn, the same recovery one-shot queries get for crashes."""
        if self._entered and self._context_size > _SESSION_TOKEN_LIMIT:
            await self._restart()
        overflows = crashes = 0
        while True:
            logger.info("session_turn_started", role=self.role)
            try:
                text = await self._run_turn(self._recap + message)
            except _ContextOverflow:
                overflows += 1
                await self._recover_overflow(overflows)
                continue
            except ProcessError as exc:
                crashes += 1
                await self._recover_crash(exc, crashes)
                continue
            except (OSError, CLIConnectionError) as exc:
                crashes += 1
                _check_os_retry(exc, self.role, crashes)
                await asyncio.sleep(_SDK_CRASH_RETRY_BACKOFF * 2 ** (crashes - 1))
                await self._restart("hit an operating-system error")
                continue
            self._recap = ""
            logger.info("session_turn_finished", role=self.role)
            return text

    async def _recover_overflow(self, overflows: int) -> None:
        """Replace a session that ran out of context, or give up."""
        if overflows >= len(_TURN_ATTEMPTS) or not self._entered:
            raise RuntimeError(
                f"{self.role} session exceeded its context window"
            ) from None
        await self._restart()

    async def _recover_crash(self, exc: ProcessError, crashes: int) -> None:
        """Replace a crashed session, or report a terminal process failure."""
        if (
            not _looks_like_a_crash(exc.exit_code)
            or not self._entered
            or crashes >= _SDK_CRASH_RETRY_ATTEMPTS
        ):
            raise RuntimeError(
                _process_error_message(
                    self.role,
                    exc,
                    crashed=_looks_like_a_crash(exc.exit_code),
                    attempt=crashes,
                )
            ) from exc
        logger.warning(
            "session_crash_retry",
            role=self.role,
            attempt=crashes,
            exit_code=exc.exit_code,
        )
        await asyncio.sleep(_SDK_CRASH_RETRY_BACKOFF * 2 ** (crashes - 1))
        await self._restart("ended unexpectedly")

    async def _run_turn(self, message: str) -> str:  # ruff: ignore[complex-structure]
        text = []
        last_call = 0
        try:
            await self._client.query(message)
            async for item in self._client.receive_response():
                log_stream_message(self.role, item, options=self._session_options)
                if isinstance(item, AssistantMessage):
                    text.extend(
                        block.text
                        for block in item.content
                        if isinstance(block, TextBlock)
                    )
                    if item.parent_tool_use_id is None:
                        last_call = _context_tokens(item) or last_call
                elif isinstance(item, ResultMessage):
                    # The result's usage totals every API call of the turn; the
                    # last call alone is what the next turn's context starts at.
                    self._context_size = (
                        last_call or _context_tokens(item) or self._context_size
                    )
                    self._check_result(item)
        except ProcessError as exc:
            if not exc.stderr and text:
                exc.stderr = text[-1]
            if _CONTEXT_OVERFLOW.search(f"{exc.stderr or ''} {exc}"):
                raise _ContextOverflow from exc
            raise
        return "\n".join(text)

    def _check_result(self, result: ResultMessage) -> None:
        """A failed turn must fail, not hand its partial text back as success."""
        failed = getattr(result, "is_error", False) is True
        if not failed and getattr(result, "subtype", "success") == "success":
            return
        detail = str(getattr(result, "result", "") or "")
        if _CONTEXT_OVERFLOW.search(detail):
            raise _ContextOverflow
        raise RuntimeError(
            f"{self.role} failed: {result.subtype}" + (f": {detail}" if detail else "")
        )


class _ContextOverflow(Exception):
    """The SDK session ran out of context window."""


class ConfigLookups:
    """Model and lint lookups every `AgentContext` derives from `config`."""

    config: dict

    def model(self, role: str, agent: CustomAgent | None = None) -> str | None:
        """`[models].<role>` if set, else the definition's model, else the
        role's configured default (None: engine default) or the reviewer's."""
        models = self.config["models"]
        configured = models.get(role)
        if configured is not None:
            return configured
        if agent is not None and agent.model:
            return agent.model
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
