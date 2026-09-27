"""
meow/sprint.py

Sprint: the per-sprint state every role reads and none of them mutate. Kept
in its own module so the agent-wiring roles and the orchestration loop can
both import it without either depending on the other.
"""

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AgentDefinition

from meow.config import LintCommand


@dataclass(frozen=True)
class Sprint:
    """What every role needs and none of them change."""

    project_root: Path
    config: dict
    explorer: AgentDefinition
    lint_hook: object

    def model(self, role: str) -> str | None:
        return self.config["models"][role]

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]
