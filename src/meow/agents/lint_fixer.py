"""Lint-fix agent: fixes raw lint-command failures in a session that
survives across rounds -- the standalone `meow run --lint-fix` path.
`--report-only` never constructs it; the calling session fixes instead."""

from claude_agent_sdk import HookMatcher

from meow.agents.base import AgentContext, SessionAgent
from meow.infrastructure.lint import make_lint_hook
from meow.project.prompts import lint_fixer_prompt


class LintFixAgent(SessionAgent):
    """Fixes reported lint failures with no explorer subagent: the report already
    says what and where."""

    role = "lint_fixer"

    def __init__(self, context: AgentContext, timeout: float | None = None):
        super().__init__(context)
        if timeout is None:
            timeout = context.config["lint_timeout"]
        lint_hook = make_lint_hook(
            context.active_working_dir(), context.lint_commands(), timeout
        )
        self._open_session(
            self.options(
                system_prompt=lint_fixer_prompt(),
                allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
                role="lint_fixer",
                hooks={
                    "PostToolUse": [
                        HookMatcher(matcher="Write|Edit", hooks=[lint_hook])
                    ]
                },
            )
        )

    async def fix(self, problems: str) -> str:
        return await self._turn(f"Fix these lint failures:\n\n{problems}")
