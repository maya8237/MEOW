"""Review-fix agent: fixes reported review findings in a session that
survives across rounds -- the prompt- and branch-based half of `meow review
--fix` (plan-based reviews reuse `GeneratorAgent`)."""

from claude_agent_sdk import HookMatcher

from meow.agents.base import AgentContext, SessionAgent
from meow.infrastructure.lint import make_lint_hook
from meow.project.prompts import review_fixer_prompt


class ReviewFixAgent(SessionAgent):
    """Fixes reported review findings with no explorer subagent: the report already
    says what and where."""

    role = "review_fixer"

    def __init__(self, context: AgentContext, timeout: float | None = None):
        super().__init__(context)
        if timeout is None:
            timeout = context.config.get("lint_timeout", 60)
        lint_hook = make_lint_hook(
            context.active_working_dir(), context.lint_commands(), timeout
        )
        self._open_session(
            self.options(
                system_prompt=review_fixer_prompt(),
                allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
                role="review_fixer",
                hooks={
                    "PostToolUse": [
                        HookMatcher(matcher="Write|Edit", hooks=[lint_hook])
                    ]
                },
            )
        )

    async def fix(self, problems: str) -> str:
        return await self._turn(f"Fix these review findings:\n\n{problems}")
