"""Issue-fetcher agent: talks to a configured Jira MCP server to confirm it
is reachable, and to pull one issue's key/summary/description to a file."""

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolUseBlock,
    query,
)

from meow.agents.base import Agent, AgentContext, log_stream_message
from meow.logging import get_logger

logger = get_logger(__name__)

JIRA_MCP_NAME = "jira"
_JIRA_TOOLS = [f"mcp__{JIRA_MCP_NAME}__*"]
_JIRA_TOOL_PREFIX = f"mcp__{JIRA_MCP_NAME}__"


def _used_jira_tool(message: object) -> bool:
    if not isinstance(message, AssistantMessage):
        return False
    return any(
        isinstance(block, ToolUseBlock) and block.name.startswith(_JIRA_TOOL_PREFIX)
        for block in message.content
    )


class IssueFetcherAgent(Agent):
    """Reads Jira issues through a `[jira.mcp]`-configured MCP server."""

    def __init__(self, context: AgentContext, mcp_config: dict):
        super().__init__(context)
        self._mcp_servers = {JIRA_MCP_NAME: mcp_config}

    def _options(
        self, *, system_prompt: str, allowed_tools: list[str]
    ) -> ClaudeAgentOptions:
        return self.options(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            role="issue_fetcher",
            mcp_servers=self._mcp_servers,
        )

    async def check_active(self) -> None:
        """Raise RuntimeError unless the Jira MCP actually answers a call.

        A text-only "success" is not enough evidence -- the model could
        simply claim success without the server being there, so this checks
        that a `mcp__jira__*` tool was actually invoked.
        """
        options = self._options(
            system_prompt=(
                "Call a tool from the configured Jira MCP server to confirm "
                "it is reachable -- for example, fetch the current user or "
                "list accessible projects. Reply with exactly 'JIRA_OK' if a "
                "tool call succeeds, or 'JIRA_FAIL: <reason>' if it does not."
            ),
            allowed_tools=_JIRA_TOOLS,
        )
        used_jira_tool = False
        succeeded = False
        logger.info("jira_preflight_query_started")
        async for message in query(
            prompt="Check Jira MCP connectivity.", options=options
        ):
            log_stream_message("issue_fetcher", message)
            used_jira_tool = used_jira_tool or _used_jira_tool(message)
            if isinstance(message, ResultMessage):
                succeeded = message.subtype == "success"

        if not used_jira_tool or not succeeded:
            raise RuntimeError(
                "No active Jira MCP server responded. Check [jira.mcp] in "
                ".harness.toml (command/args to launch it, e.g. command = "
                '"uvx", args = ["mcp-atlassian"]) and the Jira credentials '
                "it needs in the environment -- see docs/INTEGRATIONS.md."
            )
        logger.info("jira_preflight_query_passed")

    async def fetch(
        self, issue_key: str | None, project_key: str, output_file: Path
    ) -> None:
        """Write `{"key", "summary", "description"}` for one issue to a file."""
        if issue_key:
            lookup = f"Fetch the Jira issue {issue_key} by its key."
        else:
            lookup = (
                "Find the most recently created issue in the Jira project "
                f"'{project_key}' (JQL: project = {project_key} ORDER BY "
                "created DESC, take the first result)."
            )
        options = self._options(
            system_prompt=(
                "You fetch one Jira issue's details through the configured "
                "Jira MCP server and write them to a file -- nothing else. "
                f"{lookup} Then write a JSON object with exactly the keys "
                '"key", "summary", and "description" (description as plain '
                f"text, not markup) to {output_file}. Do not write any other "
                "file, and do not modify the issue."
            ),
            allowed_tools=[*_JIRA_TOOLS, "Write"],
        )
        await self.run_query("Fetch the Jira issue.", options, "issue_fetcher")
        if not output_file.exists():
            raise RuntimeError(
                f"issue-fetcher did not write {output_file} -- the Jira MCP "
                "may not have returned a matching issue."
            )
