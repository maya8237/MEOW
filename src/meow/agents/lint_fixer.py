"""Lint-fix agent: given raw lint-command failures, edits the project to
resolve them, in a session that survives across rounds -- the standalone-CLI
half of `meow run --lint-fix`. Report-only mode (the `lint-fix` skill wrapper)
never constructs this agent at all; fixing what's reported is left to the
calling Claude session there."""

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
)

from meow.agents.base import Agent, AgentContext, log_stream_message
from meow.lint import make_lint_hook
from meow.logging import get_logger
from meow.prompts import lint_fixer_prompt

logger = get_logger(__name__)


class LintFixAgent(Agent):
    """Fixes reported lint failures the same shape as `GeneratorAgent` --
    a persistent `ClaudeSDKClient` session so later rounds remember what
    earlier ones already tried -- but scoped to raw lint output instead of a
    Sprint Contract, with no explorer subagent (the failures already say
    what and where)."""

    def __init__(self, context: AgentContext, timeout: float | None = None):
        super().__init__(context)
        if timeout is None:
            timeout = context.config["lint_timeout"]
        lint_hook = make_lint_hook(
            context.active_working_dir(), context.lint_commands(), timeout
        )
        options = self.options(
            system_prompt=lint_fixer_prompt(),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
            role="lint_fixer",
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher="Write|Edit", hooks=[lint_hook])
                ]
            },
        )
        self._client = ClaudeSDKClient(options=options)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def fix(self, problems: str) -> str:
        logger.info("lint_fix_turn_started")
        await self._client.query(f"Fix these lint failures:\n\n{problems}")
        text = []
        async for message in self._client.receive_response():
            log_stream_message("lint_fixer", message)
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
        logger.info("lint_fix_turn_finished")
        return "\n".join(text)
