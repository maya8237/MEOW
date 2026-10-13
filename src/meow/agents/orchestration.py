"""Orchestrator roles: task decomposition and the factory that builds every
role the orchestrator drives (so tests and native mode can substitute stubs)."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meow.agents.base import Agent, prepare_output_file, restrict_writes
from meow.agents.fixers import ReviewFixAgent
from meow.agents.mcp_fetcher import IssueFetcherAgent
from meow.agents.reviewer import ReviewerAgent
from meow.agents.tester import VerificationAgent
from meow.execution.orchestration.model import MAX_ORCHESTRATOR_TASKS
from meow.project.prompts import decomposer_prompt, decomposer_query


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


@dataclass
class OrchestratorAgents:
    """Builds each role from an `AgentContext`; swap a field to stub a role.

    The roles for triage, conflict resolution, and pull requests are added as
    their classes are written; until then their fields stay None.
    """

    decomposer: Callable[..., DecomposerAgent] = DecomposerAgent
    triage: Callable[..., Any] | None = None
    integrator: Callable[..., Any] | None = None
    reviewer: Callable[..., ReviewerAgent] = ReviewerAgent
    fixer: Callable[..., ReviewFixAgent] = ReviewFixAgent
    tester: Callable[..., VerificationAgent] = VerificationAgent
    pull_request: Callable[..., Any] | None = None
    issue_fetcher: Callable[..., IssueFetcherAgent] = IssueFetcherAgent
