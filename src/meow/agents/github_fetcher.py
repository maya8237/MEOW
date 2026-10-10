"""GitHub pull-request fetcher agent backed by a configured MCP server."""

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolUseBlock,
    query,
)

from meow.agents.base import (
    Agent,
    AgentContext,
    log_stream_message,
    prepare_output_file,
    restrict_writes,
)
from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)

GITHUB_MCP_NAME = "github"
_GITHUB_TOOLS = [f"mcp__{GITHUB_MCP_NAME}__*"]
_GITHUB_TOOL_PREFIX = f"mcp__{GITHUB_MCP_NAME}__"


def _used_github_tool(message: object) -> bool:
    if not isinstance(message, AssistantMessage):
        return False
    return any(
        isinstance(block, ToolUseBlock) and block.name.startswith(_GITHUB_TOOL_PREFIX)
        for block in message.content
    )


class GithubFetcherAgent(Agent):
    """Read GitHub pull requests through a `[github.mcp]`-configured server."""

    def __init__(self, context: AgentContext, mcp_config: dict):
        super().__init__(context)
        self._mcp_servers = {GITHUB_MCP_NAME: mcp_config}

    def _options(
        self, *, system_prompt: str, allowed_tools: list[str]
    ) -> ClaudeAgentOptions:
        return self.options(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            role="github_fetcher",
            mcp_servers=self._mcp_servers,
        )

    async def check_active(self) -> None:
        """Require a successful tool call from the configured GitHub MCP."""
        options = self._options(
            system_prompt=(
                "Call a tool from the configured GitHub MCP server to confirm "
                "it is reachable -- for example, fetch the current user or "
                "list accessible repositories. Reply with exactly 'GITHUB_OK' "
                "if a tool call succeeds, or 'GITHUB_FAIL: <reason>' if it "
                "does not."
            ),
            allowed_tools=_GITHUB_TOOLS,
        )
        used_github_tool = False
        succeeded = False
        logger.info("github_preflight_query_started")
        async for message in query(
            prompt="Check GitHub MCP connectivity.", options=options
        ):
            log_stream_message("github_fetcher", message, options=options)
            used_github_tool = used_github_tool or _used_github_tool(message)
            if isinstance(message, ResultMessage):
                succeeded = message.subtype == "success"

        if not used_github_tool or not succeeded:
            raise RuntimeError(
                "No active GitHub MCP server responded. Check [github.mcp] "
                "in .meow/config.toml -- command/args to launch it, and "
                "[github.mcp.env] for whatever credentials it needs -- see "
                "docs/INTEGRATIONS.md."
            )
        logger.info("github_preflight_query_passed")

    async def fetch(self, pull_request_link: str, output_file: Path) -> None:
        """Write `{"title", "description", "diff"}` for one pull request."""
        options = self._options(
            system_prompt=(
                "You fetch one GitHub pull request's details through the "
                "configured GitHub MCP server and write them to a file -- "
                f"nothing else. Fetch the pull request at {pull_request_link} "
                "-- its title, body/description, and full unified diff of "
                "changes. Then write a JSON object with exactly the keys "
                '"title", "description", and "diff" (diff as plain unified-diff '
                f"text) to {output_file}. Do not write any other file, and "
                "do not modify the pull request (no comments, approvals, "
                "merges, or review submissions)."
            ),
            allowed_tools=[*_GITHUB_TOOLS, "Write"],
        )
        restrict_writes(options, output_file, self.context.active_working_dir())
        prepare_output_file(output_file)
        await self.run_query(
            "Fetch the GitHub pull request.", options, "github_fetcher"
        )
        if not output_file.is_file():
            raise RuntimeError(
                f"github-fetcher did not write {output_file} -- the GitHub "
                "MCP may not have returned a matching pull request."
            )
