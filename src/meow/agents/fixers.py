"""Scoped fixer roles that keep one session across rounds, so later rounds
remember what earlier ones tried. Neither gets an explorer subagent: the
report it is handed already says what and where.

- `ReviewFixAgent`: the prompt- and branch-based half of `meow review --fix`
  (plan-based reviews reuse `GeneratorAgent`).
- `LintFixAgent`: the standalone `meow run --lint-fix` path; `--report-only`
  never constructs it, the calling session fixes instead.
"""

from collections.abc import Callable

from claude_agent_sdk import HookMatcher

from meow.agents.base import AgentContext, SessionAgent
from meow.infrastructure.lint import make_lint_hook
from meow.project.prompts import lint_fixer_prompt, review_fixer_prompt


class _FixerAgent(SessionAgent):
    prompt: Callable[[], str]
    task = ""  # leads the problems handed to `fix`

    def __init__(self, context: AgentContext, timeout: float | None = None):
        super().__init__(context)
        lint_hook = make_lint_hook(
            context.active_working_dir(),
            context.lint_commands(),
            context.config.get("lint_timeout", 60) if timeout is None else timeout,
        )
        self._open_session(
            self.options(
                system_prompt=self.prompt(),
                allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
                role=self.role,
                hooks={
                    "PostToolUse": [
                        HookMatcher(matcher="Write|Edit", hooks=[lint_hook])
                    ]
                },
            )
        )

    async def fix(self, problems: str) -> str:
        return await self._turn(f"{self.task}\n\n{problems}")


class ReviewFixAgent(_FixerAgent):
    """Fixes reported review findings."""

    role = "review_fixer"
    prompt = staticmethod(review_fixer_prompt)
    task = "Fix these review findings:"


class LintFixAgent(_FixerAgent):
    """Fixes reported lint failures."""

    role = "lint_fixer"
    prompt = staticmethod(lint_fixer_prompt)
    task = "Fix these lint failures:"
