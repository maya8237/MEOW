"""Run any resolved agent definition without a built-in role adapter."""

from contextlib import AsyncExitStack

from claude_agent_sdk import HookMatcher

from meow.agents.base import Agent
from meow.infrastructure.lint import make_lint_hook
from meow.project.custom.rendering import PROMPT_VARIABLES, render_agent


class StageAgent(Agent):
    async def run(self, agent, task, values, pool):
        prompt = render_agent(
            agent,
            {name: value for name, value in values.items() if name in PROMPT_VARIABLES},
        )
        extra = {}
        if agent.lint_on_edit:
            hook = make_lint_hook(
                self.context.active_working_dir(),
                self.context.lint_commands(),
                self.context.config.get("lint_timeout", 60),
            )
            extra["hooks"] = {
                "PostToolUse": [HookMatcher(matcher="Write|Edit", hooks=[hook])]
            }
        options = self.definition_options(agent, system_prompt=prompt, **extra)
        if agent.session == "persistent":
            from meow.execution.workflow.engine import SessionPool

            if pool is not None:
                return await pool.run(
                    self.context, agent.role, options, prompt + "\n\n" + task
                )
            async with AsyncExitStack() as stack:
                pool = SessionPool()
                stack.push_async_callback(pool.stack.aclose)
                return await pool.run(self.context, agent.role, options, task)
        return await self.run_query(task, options, agent.role)
