"""Read-only fetcher roles backed by a configured MCP server.

`McpFetcherAgent` holds everything the Jira, GitLab and GitHub fetchers share:
a connectivity preflight that requires a real `mcp__<server>__*` tool call
(a text-only "success" proves nothing), and a fetch that may write only its
own JSON output file. Each subclass names its server, role and request kind.
"""

import json
import tempfile
from collections.abc import Awaitable, Callable
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
from meow.project.prompts import mcp_fetch_prompt, mcp_probe_prompt

logger = get_logger(__name__)


class McpFetcherAgent(Agent):
    """Fetch one record through the MCP server named by `server`."""

    server = ""  # MCP server name and tool prefix, e.g. "jira"
    role = ""  # permission and model role, e.g. "issue_fetcher"
    label = ""  # human name, e.g. "Jira"
    request = ""  # what one fetch returns, e.g. "issue"
    probe = "fetch the current user or list accessible projects"
    setup_hint = ""  # how to fix a failed preflight

    def __init__(self, context: AgentContext, mcp_config: dict):
        super().__init__(context)
        self._mcp_servers = {self.server: mcp_config}

    @property
    def _tools(self) -> list[str]:
        return [f"mcp__{self.server}__*"]

    def _options(
        self, *, system_prompt: str, allowed_tools: list[str]
    ) -> ClaudeAgentOptions:
        return self.options(
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            role=self.role,
            mcp_servers=self._mcp_servers,
        )

    def _used_server_tool(self, message: object) -> bool:
        prefix = f"mcp__{self.server}__"
        return isinstance(message, AssistantMessage) and any(
            isinstance(block, ToolUseBlock) and block.name.startswith(prefix)
            for block in message.content
        )

    async def check_active(self) -> None:
        """Raise RuntimeError unless the MCP server actually answers a call."""
        options = self._options(
            system_prompt=mcp_probe_prompt(
                self.label, self.server.upper(), self.probe
            ),
            allowed_tools=self._tools,
        )
        used_tool = succeeded = False
        logger.info("mcp_preflight_started", server=self.server)
        async for message in query(
            prompt=f"Check {self.label} MCP connectivity.", options=options
        ):
            log_stream_message(self.role, message, options=options)
            used_tool = used_tool or self._used_server_tool(message)
            if isinstance(message, ResultMessage):
                succeeded = message.subtype == "success"
        if not used_tool or not succeeded:
            raise RuntimeError(
                f"No active {self.label} MCP server responded. {self.setup_hint}"
            )
        logger.info("mcp_preflight_passed", server=self.server)

    async def _fetch(
        self, lookup: str, fields: str, forbidden: str, output_file: Path
    ) -> None:
        options = self._options(
            system_prompt=mcp_fetch_prompt(
                self.label, self.request, lookup, fields, output_file, forbidden
            ),
            allowed_tools=[*self._tools, "Write"],
        )
        restrict_writes(options, output_file, self.context.active_working_dir())
        prepare_output_file(output_file)
        await self.run_query(
            f"Fetch the {self.label} {self.request}.", options, self.role
        )
        if not output_file.is_file():
            raise RuntimeError(
                f"{self.role} did not write {output_file} -- the {self.label} MCP "
                f"may not have returned a matching {self.request}."
            )


class IssueFetcherAgent(McpFetcherAgent):
    """Reads Jira issues through a `[jira.mcp]`-configured MCP server."""

    server, role, label, request = "jira", "issue_fetcher", "Jira", "issue"
    setup_hint = (
        "Check [jira.mcp] in .meow/config.toml (command/args to launch it, e.g. "
        'command = "uvx", args = ["mcp-atlassian"]) and the Jira credentials it '
        "needs in the environment -- see docs/INTEGRATIONS.md."
    )

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
        await self._fetch(
            lookup,
            '"key", "summary", and "description" (description as plain text, '
            "not markup)",
            "",
            output_file,
        )


_DIFF_FIELDS = '"title", "description", and "diff" (diff as plain unified-diff text)'


class GitlabFetcherAgent(McpFetcherAgent):
    """Reads GitLab merge requests through a `[gitlab.mcp]`-configured server."""

    server, role, label, request = "gitlab", "gitlab_fetcher", "GitLab", "merge request"
    setup_hint = (
        "Check [gitlab.mcp] in .meow/config.toml -- command/args to launch it, "
        "and [gitlab.mcp.env] for whatever credentials it needs -- see "
        "docs/INTEGRATIONS.md."
    )

    async def fetch(self, mr_link: str, output_file: Path) -> None:
        """Write `{"title", "description", "diff"}` for one MR to a file."""
        await self._fetch(
            f"Fetch the merge request at {mr_link} -- its title, description, "
            "and full unified diff of changes.",
            _DIFF_FIELDS,
            " (no comments, approvals, or merges)",
            output_file,
        )


class GithubFetcherAgent(McpFetcherAgent):
    """Reads GitHub pull requests through a `[github.mcp]`-configured server."""

    server, role, label, request = "github", "github_fetcher", "GitHub", "pull request"
    probe = "fetch the current user or list accessible repositories"
    setup_hint = (
        "Check [github.mcp] in .meow/config.toml -- command/args to launch it, "
        "and [github.mcp.env] for whatever credentials it needs -- see "
        "docs/INTEGRATIONS.md."
    )

    async def fetch(self, pull_request_link: str, output_file: Path) -> None:
        """Write `{"title", "description", "diff"}` for one pull request."""
        await self._fetch(
            f"Fetch the pull request at {pull_request_link} -- its title, "
            "body/description, and full unified diff of changes.",
            _DIFF_FIELDS,
            " (no comments, approvals, merges, or review submissions)",
            output_file,
        )


async def fetch_record(
    fetcher: McpFetcherAgent,
    fetch: Callable[[Path], Awaitable[None]],
    required: tuple[str, ...],
) -> dict:
    """Preflight `fetcher`, run `fetch` into a temp file, and validate the JSON."""
    await fetcher.check_active()
    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / f"{fetcher.server}.json"
        await fetch(output_file)
        data = json.loads(output_file.read_text(encoding="utf-8"))
    missing = [field for field in required if data.get(field) is None]
    if missing:
        raise RuntimeError(f"{fetcher.role} output is missing {missing}: {data}")
    return data
