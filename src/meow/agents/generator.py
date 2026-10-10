"""Generator agent setup and resumable implementation sessions."""

from pathlib import Path

from claude_agent_sdk import HookMatcher

from meow.agents.base import GeneratorContext, SessionAgent
from meow.project.prompts import generator_prompt

_TOOLS = ("Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent")


class GeneratorAgent(SessionAgent):
    role = "generator"

    def __init__(self, context: GeneratorContext, plan_file: Path):
        super().__init__(context)
        self._open_session(
            self.options(
                system_prompt=generator_prompt(plan_file),
                allowed_tools=list(_TOOLS),
                role="generator",
                agents={
                    **self.custom_agents("generator", list(_TOOLS)),
                    "explorer": context.explorer,
                },
                skills=[
                    "superpowers:executing-plans",
                    "superpowers:test-driven-development",
                    "superpowers:systematic-debugging",
                    "superpowers:receiving-code-review",
                    "superpowers:verification-before-completion",
                ],
                hooks={
                    "PostToolUse": [
                        HookMatcher(matcher="Write|Edit", hooks=[context.lint_hook])
                    ]
                },
            )
        )

    async def implement(self, instruction: str) -> str:
        return await self._turn(instruction)


Generator = GeneratorAgent
