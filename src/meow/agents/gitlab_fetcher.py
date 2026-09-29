"""GitLab-review fetcher agent: talks to a configured GitLab MCP server to
confirm it is reachable, and to pull one merge request's title, description,
and diff to a file."""

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

GITLAB_MCP_NAME = "gitlab"
_GITLAB_TOOLS = [f"mcp__{GITLAB_MCP_NAME}__*"]
_GITLAB_TOOL_PREFIX = f"mcp__{GITLAB_MCP_NAME}__"


def _used_gitlab_tool(message: object) -> bool:
    if not isinstance(message, AssistantMessage):
        return False
    return any(
        isinstance(block, ToolUseBlock) and block.name.startswith(_GITLAB_TOOL_PREFIX)
        for block in message.content
    )


class GitlabFetcherAgent(Agent):
    """Reads GitLab merge requests through a `[gitlab.mcp]`-configured MCP server."""

    def __init__(self, context: AgentContext, mcp_config: dict):
        super().__init__(context)
        self._mcp_servers = {GITLAB_MCP_NAME: mcp_config}

    def _options(
        self, *, system_prompt: str, allowed_tools: list[str]
    ) -> ClaudeAgentOptions:
        return self.options(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            role="gitlab_fetcher",
            mcp_servers=self._mcp_servers,
        )

    async def check_active(self) -> None:
        """Raise RuntimeError unless the GitLab MCP actually answers a call.

        A text-only "success" is not enough evidence -- the model could
        simply claim success without the server being there, so this checks
        that a `mcp__gitlab__*` tool was actually invoked.
        """
        options = self._options(
            system_prompt=(
                "Call a tool from the configured GitLab MCP server to confirm "
                "it is reachable -- for example, fetch the current user or "
                "list accessible projects. Reply with exactly 'GITLAB_OK' if "
                "a tool call succeeds, or 'GITLAB_FAIL: <reason>' if it does "
                "not."
            ),
            allowed_tools=_GITLAB_TOOLS,
        )
        used_gitlab_tool = False
        succeeded = False
        logger.info("gitlab_preflight_query_started")
        async for message in query(
            prompt="Check GitLab MCP connectivity.", options=options
        ):
            log_stream_message("gitlab_fetcher", message)
            used_gitlab_tool = used_gitlab_tool or _used_gitlab_tool(message)
            if isinstance(message, ResultMessage):
                succeeded = message.subtype == "success"

        if not used_gitlab_tool or not succeeded:
            raise RuntimeError(
                "No active GitLab MCP server responded. Check [gitlab.mcp] "
                "in .harness.toml -- command/args to launch it, and "
                "[gitlab.mcp.env] for whatever credentials it needs -- see "
                "GUIDE.md."
            )
        logger.info("gitlab_preflight_query_passed")

    async def fetch(self, mr_link: str, output_file: Path) -> None:
        """Write `{"title", "description", "diff"}` for one MR to a file."""
        options = self._options(
            system_prompt=(
                "You fetch one GitLab merge request's details through the "
                "configured GitLab MCP server and write them to a file -- "
                f"nothing else. Fetch the merge request at {mr_link} -- its "
                "title, description, and full unified diff of changes. Then "
                'write a JSON object with exactly the keys "title", '
                '"description", and "diff" (diff as plain unified-diff '
                f"text) to {output_file}. Do not write any other file, and "
                "do not modify the merge request (no comments, no "
                "approvals, no merges)."
            ),
            allowed_tools=[*_GITLAB_TOOLS, "Write"],
        )
        await self.run_query(
            "Fetch the GitLab merge request.", options, "gitlab_fetcher"
        )
        if not output_file.exists():
            raise RuntimeError(
                f"gitlab-fetcher did not write {output_file} -- the GitLab "
                "MCP may not have returned a matching merge request."
            )
