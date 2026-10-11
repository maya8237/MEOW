"""Generator agent setup and resumable implementation sessions."""

from pathlib import Path

from claude_agent_sdk import HookMatcher

from meow.agents.base import GeneratorContext, SessionAgent
from meow.project.prompts import generator_prompt


class GeneratorAgent(SessionAgent):
    role = "generator"

    def __init__(self, context: GeneratorContext, plan_file: Path):
        super().__init__(context)
        self.plan_file = plan_file
        agent = self.definition("generator")
        extra = {}
        if agent.lint_on_edit:
            extra["hooks"] = {
                "PostToolUse": [
                    HookMatcher(matcher="Write|Edit", hooks=[context.lint_hook])
                ]
            }
        self._open_session(
            self.definition_options(
                agent,
                system_prompt=generator_prompt(plan_file, agent=agent),
                **extra,
            )
        )

    async def implement(self, instruction: str) -> str:
        agent = self.definition("generator")
        if agent.source.scope != "builtin":
            instruction = (
                f"{generator_prompt(self.plan_file, agent=agent)}\n\n{instruction}"
            )
        return await self._turn(instruction)


Generator = GeneratorAgent
