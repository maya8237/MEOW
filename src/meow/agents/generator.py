"""Generator agent setup and resumable implementation sessions."""

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
)

from meow.agents.base import Agent, GeneratorContext, log_stream_message
from meow.logging import get_logger
from meow.prompts import generator_prompt

logger = get_logger(__name__)


class GeneratorAgent(Agent):
    def __init__(self, context: GeneratorContext, plan_file: Path):
        super().__init__(context)
        options = self.options(
            system_prompt=generator_prompt(plan_file),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"],
            role="generator",
            agents={"explorer": context.explorer},
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
        self._client = ClaudeSDKClient(options=options)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def implement(self, instruction: str) -> str:
        logger.info("generator_turn_started")
        await self._client.query(instruction)
        text = []
        async for message in self._client.receive_response():
            log_stream_message("generator", message)
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
        logger.info("generator_turn_finished")
        return "\n".join(text)


Generator = GeneratorAgent
