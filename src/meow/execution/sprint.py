"""The immutable Sprint state shared by each agent, and how to build one.

`build_sprint` wires up the explorer definition and lint hook that live on a
`Sprint`; it stays here, next to the dataclass it constructs, rather than in
`orchestrator.py`, which only coordinates the generator/reviewer round loop.
"""

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AgentDefinition

from meow.agents.explorer import make_explorer_agent
from meow.infrastructure.lint import make_lint_hook
from meow.project.config import LintCommand


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
        models = self.config["models"]
        return models.get(role, models.get("reviewer"))

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]

    def active_working_dir(self) -> Path:
        return self.working_dir or self.repo_dir


def build_sprint(
    repo_dir: Path,
    config: dict,
    working_dir: Path | None = None,
    *,
    use_worktree: bool = False,
) -> Sprint:
    """Wire up the explorer definition and lint hook, and assemble a Sprint."""
    commands = config["lint"]
    active_dir = working_dir or repo_dir
    return Sprint(
        repo_dir=repo_dir,
        config=config,
        explorer=make_explorer_agent(config, active_dir),
        lint_hook=make_lint_hook(active_dir, commands, config["lint_timeout"]),
        working_dir=active_dir,
        use_worktree=use_worktree,
    )
