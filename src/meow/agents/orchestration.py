"""Orchestrator roles: task decomposition and the factory that builds every
role the orchestrator drives (so tests and native mode can substitute stubs)."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meow.agents.base import (
    Agent,
    guard_tools,
    prepare_output_file,
    restrict_writes,
    tool_input_path,
)
from meow.agents.fixers import ReviewFixAgent
from meow.agents.mcp_fetcher import IssueFetcherAgent
from meow.agents.reviewer import ReviewerAgent
from meow.agents.tester import VerificationAgent
from meow.execution.orchestration.model import MAX_ORCHESTRATOR_TASKS
from meow.project.prompts import (
    decomposer_prompt,
    decomposer_query,
    integrator_prompt,
    integrator_query,
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
    pull_request: Callable[..., Any] | None = None
    issue_fetcher: Callable[..., IssueFetcherAgent] = IssueFetcherAgent
