"""Orchestrator roles: task decomposition and the factory that builds every
role the orchestrator drives (so tests and native mode can substitute stubs)."""

from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

from meow.agents.base import (
    Agent,
    AgentContext,
    allow_only_tools,
    guard_tools,
    prepare_output_file,
    restrict_writes,
    tool_input_path,
)
from meow.agents.fixers import ReviewFixAgent
from meow.agents.mcp_fetcher import IssueFetcherAgent, McpFetcherAgent, _parse_record
from meow.agents.reviewer import ReviewerAgent
from meow.agents.tester import VerificationAgent
from meow.execution.orchestration.model import MAX_ORCHESTRATOR_TASKS
from meow.project.prompts import (
    decomposer_prompt,
    decomposer_query,
    integrator_prompt,
    integrator_query,
    pull_request_prompt,
    triage_prompt,
    triage_query,
)


class DecomposerAgent(Agent):
    """Reads the repository and writes the orchestrator's task graph."""

    async def run(
        self,
        objective: str,
        output_file: Path,
        *,
        task_drafts: str | None = None,
        correction: str | None = None,
    ) -> Path:
        """Write the tasks document for `objective` to `output_file`.

        `task_drafts` fixes the task list (Jira sets); `correction` carries the
        validation error of a rejected earlier attempt.
        """
        agent = self.definition("decomposer")
        options = self.definition_options(
            agent,
            system_prompt=decomposer_prompt(
                output_file,
                max_tasks=MAX_ORCHESTRATOR_TASKS,
                task_drafts=task_drafts,
                agent=agent,
            ),
        )
        restrict_writes(options, output_file, self.context.active_working_dir())
        prepare_output_file(output_file)
        await self.run_definition_query(
            agent,
            decomposer_query(objective, correction=correction),
            options,
            "Decomposer",
        )
        if not output_file.is_file():
            raise RuntimeError(f"Decomposer did not write {output_file}")
        return output_file


class TriageAgent(Agent):
    """Reads a failed attempt's evidence and writes a recovery decision file."""

    async def run(  # ruff: ignore[too-many-arguments]
        self,
        task_request: str,
        cause: str,
        evidence: list[str],
        output_file: Path,
        *,
        cycles: int,
        max_cycles: int,
    ) -> Path:
        agent = self.definition("triage")
        options = self.definition_options(
            agent, system_prompt=triage_prompt(output_file, agent=agent)
        )
        restrict_writes(options, output_file, self.context.active_working_dir())
        output_file.parent.mkdir(parents=True, exist_ok=True)
        prepare_output_file(output_file)
        await self.run_query(
            triage_query(task_request, cause, evidence, cycles, max_cycles),
            options,
            "Triage",
        )
        return output_file


class IntegratorAgent(Agent):
    """Resolves the conflicts of one merge inside the integration worktree."""

    async def resolve(
        self,
        conflicted_paths: list[str],
        task_request: str,
        merged_requests: list[str],
    ) -> None:
        active = self.context.active_working_dir()
        allowed = {(active / path).resolve() for path in conflicted_paths}
        agent = self.definition("integrator")
        options = self.definition_options(
            agent,
            system_prompt=integrator_prompt(active, conflicted_paths, agent=agent),
        )
        guard_tools(
            options,
            {"Write", "Edit"},
            lambda tool_input: (
                None
                if tool_input_path(tool_input, active) in allowed
                else "The integrator may edit only the conflicted files: "
                + ", ".join(conflicted_paths)
            ),
        )
        await self.run_query(
            integrator_query(conflicted_paths, task_request, merged_requests),
            options,
            "Integrator",
        )


# MCP tool names differ per server, so match by the verbs a PR needs.
_PULL_REQUEST_TOOLS = (
    "*create_pull_request*",
    "*create_merge_request*",
    "*list_pull_requests*",
    "*list_merge_requests*",
    "*search_pull_requests*",
)
_PROVIDERS = {
    "github": ("GitHub", "pull request"),
    "gitlab": ("GitLab", "merge request"),
}


class PullRequestAgent(McpFetcherAgent):
    """Opens the pull/merge request for the integration branch.

    It may use only the provider's create/list/search tools and write only its
    own output file, so a confused model cannot merge or close anything.
    """

    role = "pull_request"

    def __init__(
        self,
        context: AgentContext,
        mcp_config: dict,
        provider: Literal["github", "gitlab"],
    ):
        self.server = provider
        self.label, self.request = _PROVIDERS[provider]
        super().__init__(context, mcp_config)

    def _permitted(self, tool: str) -> bool:
        return tool == "Write" or any(
            fnmatchcase(tool, f"mcp__{self.server}__{pattern}")
            for pattern in _PULL_REQUEST_TOOLS
        )

    async def open(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per PR field
        self, title: str, body: str, head: str, base: str, output_file: Path
    ) -> str:
        """Open (or find) the PR/MR for `head` into `base`; return its URL."""
        options = self._options(
            system_prompt=pull_request_prompt(
                self.label, self.request, head, base, output_file
            ),
            allowed_tools=[*self._tools, "Write"],
        )
        allow_only_tools(
            options,
            self._permitted,
            f"Only {self.label} {self.request} tools and the output file may be used",
        )
        restrict_writes(options, output_file, self.context.active_working_dir())
        prepare_output_file(output_file)
        await self.run_query(
            f"Open the {self.request}.\n\nTitle: {title}\n\nBody:\n{body}",
            options,
            self.role,
        )
        if not output_file.is_file():
            raise RuntimeError(
                f"{self.role} did not write {output_file} -- the {self.label} MCP "
                f"may not have created the {self.request}."
            )
        url = _parse_record(self.role, output_file.read_text(encoding="utf-8")).get(
            "url"
        )
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise RuntimeError(f"{self.role} output has no valid 'url': {url!r}")
        return url


@dataclass
class OrchestratorAgents:
    """Builds each role from an `AgentContext`; swap a field to stub a role.

    The roles for triage, conflict resolution, and pull requests are added as
    their classes are written; until then their fields stay None.
    """

    decomposer: Callable[..., DecomposerAgent] = DecomposerAgent
    triage: Callable[..., Any] | None = TriageAgent
    integrator: Callable[..., Any] | None = IntegratorAgent
    reviewer: Callable[..., ReviewerAgent] = ReviewerAgent
    fixer: Callable[..., ReviewFixAgent] = ReviewFixAgent
    tester: Callable[..., VerificationAgent] = VerificationAgent
    pull_request: Callable[..., Any] | None = PullRequestAgent
    issue_fetcher: Callable[..., IssueFetcherAgent] = IssueFetcherAgent
