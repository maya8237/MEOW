"""Explorer agent definition and setup."""

from claude_agent_sdk import AgentDefinition

from meow.agents.base import Agent, AgentContext
from meow.project.prompts import explorer_prompt


class ExplorerAgent(Agent):
    """Read-only codebase exploration available to other agents."""

    def definition(self) -> AgentDefinition:
        prompt = explorer_prompt(self.context.active_working_dir())
        return AgentDefinition(
            description=(
                "Read-only codebase/log/test-output exploration. Use for any "
                "research whose raw output doesn't need to be kept in full."
            ),
            prompt=prompt,
            tools=["Read", "Grep", "Glob", "Bash"],
            model=self.context.model("explorer"),
            skills=self.skills("explorer", ["superpowers:systematic-debugging"]),
        )


def make_explorer_agent(context: AgentContext) -> AgentDefinition:
    """Build the explorer definition from the canonical agent context."""
    return ExplorerAgent(context).definition()
