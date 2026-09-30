"""Review-fix agent: given review findings, edits the project to resolve
them in a session that survives across rounds -- the prompt-based half of
`meow run --review --fix` (no plan file/Sprint Contract to hand a
GeneratorAgent, unlike the plan-based half, which reuses GeneratorAgent
via `_run_review_rounds` directly). Shaped identically to
`agents/lint_fixer.py`'s `LintFixAgent` for the same reason: a persistent,
plan-free session with a generic "fix what's reported" system prompt."""

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
)

from meow.agents.base import Agent, AgentContext, log_stream_message
from meow.lint import make_lint_hook
from meow.logging import get_logger
from meow.prompts import review_fixer_prompt

logger = get_logger(__name__)


class ReviewFixAgent(Agent):
    """Fixes reported review findings the same shape as `LintFixAgent` --
    a persistent `ClaudeSDKClient` session so later rounds remember what
    earlier ones already tried -- but scoped to review feedback instead of
    lint output, with no explorer subagent (the findings already say what
    and where)."""

    def __init__(self, context: AgentContext, timeout: float | None = None):
        super().__init__(context)
        if timeout is None:
            timeout = context.config["lint_timeout"]
        lint_hook = make_lint_hook(
            context.active_working_dir(), context.lint_commands(), timeout
        )
        options = self.options(
            system_prompt=review_fixer_prompt(),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
            role="review_fixer",
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

    async def fix(self, findings: str) -> str:
        logger.info("review_fix_turn_started")
        await self._client.query(f"Fix these review findings:\n\n{findings}")
        text = []
        async for message in self._client.receive_response():
            log_stream_message("review_fixer", message)
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
        logger.info("review_fix_turn_finished")
        return "\n".join(text)
