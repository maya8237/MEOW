"""Immutable sprint definition shared by each agent and the orchestrator."""

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AgentDefinition

from meow.config import LintCommand


@dataclass(frozen=True)
class Sprint:
    """What every role needs and none of them change."""

    repo_dir: Path
    config: dict
    explorer: AgentDefinition
    lint_hook: object
    working_dir: Path | None = None
    use_worktree: bool = False
    def model(self, role: str) -> str | None:
        return self.config["models"][role]

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]

    def active_working_dir(self) -> Path:
        return self.working_dir or self.repo_dir
