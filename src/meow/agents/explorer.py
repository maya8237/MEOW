"""Explorer agent definition and setup."""

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AgentDefinition

from meow.agents.base import Agent
from meow.rules import load_rules


class ExplorerAgent(Agent):
    """Read-only codebase exploration available to other agents."""

    def definition(self) -> AgentDefinition:
        prompt = (
            "You are a read-only research agent. Investigate the question "
            "you're given within this project directory: "
            f"{self.context.active_working_dir()}. "
            "Treat it as the project root and resolve relative paths from "
            "it. Then return only a concise summary with "
            "file:line references -- never dump raw file contents or full "
            "command output unless specifically asked to. When the task is "
            "about a bug, use systematic debugging to gather evidence and "
            "trace likely causes; stay read-only and report findings."
        )
        rules_text = load_rules(self.context.active_working_dir(), "explorer")
        if rules_text:
            prompt = f"{prompt}{rules_text}"
        return AgentDefinition(
            description=(
                "Read-only codebase/log/test-output exploration. Use for any "
                "research whose raw output doesn't need to be kept in full."
            ),
            prompt=prompt,
            tools=["Read", "Grep", "Glob", "Bash"],
            model=self.context.model("explorer"),
            skills=["superpowers:systematic-debugging"],
        )

    @classmethod
    def from_legacy(cls, config: dict, working_dir: Path) -> AgentDefinition:
        """Build the old function result from config and a working directory."""
        return cls(_LegacyContext(config, working_dir)).definition()


@dataclass(frozen=True)
class _LegacyContext:
    config: dict
    working_dir: Path

    def model(self, role: str) -> str | None:
        return self.config["models"][role]

    def active_working_dir(self) -> Path:
        return self.working_dir


def make_explorer_agent(config: dict, working_dir: Path) -> AgentDefinition:
    return ExplorerAgent.from_legacy(config, working_dir)
