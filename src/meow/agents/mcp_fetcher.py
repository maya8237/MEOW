"""Read-only fetcher roles backed by a configured MCP server.

`McpFetcherAgent` holds everything the Jira, GitLab and GitHub fetchers share:
a connectivity preflight that requires a real `mcp__<server>__*` tool call
with a non-error result (a text-only "success" proves nothing), and a fetch
that may write only its own JSON output file and must also get such a result.
A fetch without one (the server crashed or failed) is retried once; each
query launches a fresh server. Each subclass names its server, role and
request kind.
"""

import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
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

_FETCH_ATTEMPTS = 2


class _ServerCalls:
    """Observe a stream; `succeeded` once a server tool returns a non-error result."""

    def __init__(self, server: str):
        self._prefix = f"mcp__{server}__"
        self._pending: set[str] = set()
        self.succeeded = False

    def __call__(self, message: object) -> None:
        if isinstance(message, AssistantMessage):
            self._pending.update(
                block.id
                for block in message.content
                if isinstance(block, ToolUseBlock)
                and block.name.startswith(self._prefix)
            )
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            self.succeeded = self.succeeded or any(
                isinstance(block, ToolResultBlock)
                and block.tool_use_id in self._pending
                and not block.is_error
                for block in message.content
            )


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

    async def check_active(self) -> None:
        """Raise RuntimeError unless the MCP server actually answers a call."""
        options = self._options(
            system_prompt=mcp_probe_prompt(self.label, self.server.upper(), self.probe),
            allowed_tools=self._tools,
        )
        calls = _ServerCalls(self.server)
        succeeded = False
        logger.info("mcp_preflight_started", server=self.server)
        async for message in query(
            prompt=f"Check {self.label} MCP connectivity.", options=options
        ):
            log_stream_message(self.role, message, options=options)
            calls(message)
            if isinstance(message, ResultMessage):
                succeeded = message.subtype == "success"
        if not calls.succeeded or not succeeded:
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
        for attempt in range(1, _FETCH_ATTEMPTS + 1):
            prepare_output_file(output_file)
            calls = _ServerCalls(self.server)
            await self.run_query(
                f"Fetch the {self.label} {self.request}.",
                options,
                self.role,
                observe=calls,
            )
            if calls.succeeded:
                break
            logger.warning("mcp_fetch_no_result", server=self.server, attempt=attempt)
        else:
            output_file.unlink(missing_ok=True)
            raise RuntimeError(
                f"{self.role} got no successful {self.label} MCP result in "
                f"{_FETCH_ATTEMPTS} attempts -- the server may have crashed or "
                f"rejected every call. {self.setup_hint}"
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

    async def fetch_many(self, jql: str, output_file: Path) -> None:
        """Write `{"issues": [{"key", "summary", "description"}]}` for a JQL search."""
        await self._fetch(
            f"Search Jira with this JQL query: {jql} -- page through the results "
            "and collect every matching issue, at most 100.",
            '"issues" -- an array holding one object per matching issue, each with '
            'exactly the keys "key", "summary", and "description" (description as '
            "plain text, not markup)",
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


_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _as_text(value: object) -> object:
    """A fetched field as text: lists join by line, objects serialize."""
    if isinstance(value, list):
        return "\n".join(str(_as_text(item)) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def _parse_json(role: str, text: str) -> object:
    """The JSON value a fetcher wrote, tolerating a markdown code fence."""
    fenced = _FENCE.match(text)
    try:
        return json.loads(fenced.group(1) if fenced else text)
    except ValueError as exc:
        raise RuntimeError(f"{role} output is not valid JSON: {exc}") from exc


def _parse_record(role: str, text: str) -> dict:
    """The JSON object a fetcher wrote, tolerating a markdown code fence."""
    data = _parse_json(role, text)
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{role} output must be a JSON object, got {type(data).__name__}"
        )
    return {key: _as_text(value) for key, value in data.items()}


async def fetch_record(
    fetcher: McpFetcherAgent,
    fetch: Callable[[Path], Awaitable[None]],
    required: tuple[str, ...],
    *,
    preflight: bool = True,
) -> dict:
    """Preflight `fetcher` (unless `preflight` is False because the caller
    already did), run `fetch` into a temp file, and validate the JSON."""
    if preflight:
        await fetcher.check_active()
    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / f"{fetcher.server}.json"
        await fetch(output_file)
        data = _parse_record(fetcher.role, output_file.read_text(encoding="utf-8"))
    missing = [field for field in required if data.get(field) is None]
    if missing:
        raise RuntimeError(f"{fetcher.role} output is missing {missing}: {data}")
    return data


def _issue(role: str, item: object) -> dict:
    """One `{"key", "summary", "description"}` entry of a fetched issue list."""
    if not isinstance(item, dict):
        raise RuntimeError(f"{role} output issue must be a JSON object: {item!r}")
    issue = {name: _as_text(item.get(name)) for name in ("key", "summary")}
    if not all(isinstance(value, str) and value.strip() for value in issue.values()):
        raise RuntimeError(f"{role} output issue needs a key and a summary: {item!r}")
    description = _as_text(item.get("description"))
    return {**issue, "description": description if isinstance(description, str) else ""}


async def fetch_issue_list(fetcher: IssueFetcherAgent, jql: str) -> list[dict]:
    """Preflight `fetcher`, run a JQL search, and return its issues as text records."""
    await fetcher.check_active()
    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "jira-issues.json"
        await fetcher.fetch_many(jql, output_file)
        data = _parse_json(fetcher.role, output_file.read_text(encoding="utf-8"))
    issues = data.get("issues") if isinstance(data, dict) else data
    if not isinstance(issues, list):
        raise RuntimeError(
            f"{fetcher.role} output must hold an 'issues' list, got "
            f"{type(issues).__name__}"
        )
    return [_issue(fetcher.role, item) for item in issues]
