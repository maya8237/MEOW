"""Shared setup and one-shot execution for MEOW agents."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage, query

from meow.config import LintCommand


class AgentContext(Protocol):
    """Project configuration every agent role needs, independent of sprint
    lifecycle -- what `Agent.options()` reads, nothing role-specific."""

    @property
    def config(self) -> dict: ...

    @property
    def repo_dir(self) -> Path: ...

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
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model=self.context.model(role),
            cwd=str(self.context.active_working_dir()),
            **extra_options,
        )

    @staticmethod
    async def run_query(
        prompt: str, options: ClaudeAgentOptions, role: str
    ) -> None:
        """Run a one-shot SDK query and raise when the SDK reports failure."""
        messages: AsyncIterator = query(prompt=prompt, options=options)
        async for message in messages:
            if isinstance(message, ResultMessage) and message.subtype != "success":
                raise RuntimeError(f"{role} failed: {message.subtype}")


class ProjectContext:
    """Config- and directory-only `AgentContext` for non-sprint flows.

    Built from project configuration and a directory alone, with no sprint
    object, plan file, or contract dependency -- used by review operations
    that don't loop a generator against a Sprint Contract, such as `cr`.
    """

    def __init__(self, repo_dir: Path, config: dict):
        self.repo_dir = repo_dir
        self.config = config

    def model(self, role: str) -> str | None:
        return self.config["models"][role]

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]

    def active_working_dir(self) -> Path:
        return self.repo_dir
