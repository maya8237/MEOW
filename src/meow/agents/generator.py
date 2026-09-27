"""Generator agent setup and resumable implementation sessions."""

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
)

from meow.sprint import Sprint


class Generator:
    def __init__(self, sprint: Sprint, plan_file: Path):
        options = ClaudeAgentOptions(
            system_prompt=(
                f"You implement tasks from {plan_file} one at a time. "
                "Work against the agreed Sprint Contract criteria exactly "
                "-- do not expand scope. When you believe a task is "
                "complete, say so explicitly and stop; do not grade your "
                "own work."
            ),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"],
            agents={"explorer": sprint.explorer},
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher="Write|Edit", hooks=[sprint.lint_hook])
                ]
            },
            model=sprint.model("generator"),
            cwd=str(sprint.active_working_dir()),
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
