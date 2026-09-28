"""Generator agent setup and resumable implementation sessions."""

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
)

from meow.agents.base import Agent, GeneratorContext


class GeneratorAgent(Agent):
    def __init__(self, context: GeneratorContext, plan_file: Path):
        super().__init__(context)
        options = self.options(
            system_prompt=(
                f"You implement tasks from {plan_file} one at a time. "
                "Work against the agreed Sprint Contract criteria exactly "
                "-- do not expand scope. When you believe a task is "
                "complete, say so explicitly and stop; do not grade your "
                "own work. Use executing-plans to work through the supplied "
                "plan one task at a time and check each expected result. "
                "MEOW's orchestrator owns worktree setup, review rounds, and "
                "stopping control; do not create a separate ledger or commits. "
                "Use test-driven development for code changes: write a failing "
                "test, confirm the expected failure, implement, then run it "
                "and confirm it passes. When an unexpected failure occurs, "
                "use systematic debugging to establish its root cause before "
                "editing. Before acting on reviewer feedback, verify each "
                "finding against the code and plan; report unsupported or "
                "out-of-scope findings instead of making unrelated changes. "
                "Before reporting a task complete, run relevant project tests "
                "and report actual command results. Do not declare sprint "
                "success; the reviewer decides."
            ),
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
        await self._client.query(instruction)
        text = []
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
        return "\n".join(text)


Generator = GeneratorAgent
