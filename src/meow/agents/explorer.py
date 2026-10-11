"""Explorer agent definition and setup."""

from claude_agent_sdk import AgentDefinition

from meow.agents.base import Agent, AgentContext
from meow.project.prompts import explorer_prompt


class ExplorerAgent(Agent):
    """Read-only codebase exploration available to other agents."""

    def definition(self, name: str | None = None):
        if name is not None:
            return super().definition(name)
        agent = super().definition("explorer")
        prompt = explorer_prompt(self.context.active_working_dir(), agent=agent)
        return AgentDefinition(
            description=" ".join(agent.description.split()),
            prompt=prompt,
            tools=list(agent.tools or ()),
            model=self.context.model(agent.role, agent),
            skills=self.skills(agent.role, list(agent.skills)),
        )


def make_explorer_agent(context: AgentContext) -> AgentDefinition:
    """Build the explorer definition from the canonical agent context."""
    return ExplorerAgent(context).definition()
