"""Stream messages for one `mcp__<server>__*` tool call and its result."""

from claude_agent_sdk import (
    AssistantMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def server_call(
    server: str, *, is_error: bool = False, call_id: str = "call-1"
) -> list[AssistantMessage | UserMessage]:
    return [
        AssistantMessage(
            content=[ToolUseBlock(id=call_id, name=f"mcp__{server}__get", input={})],
            model="model",
        ),
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id=call_id, content="ok", is_error=is_error)
            ]
        ),
    ]
