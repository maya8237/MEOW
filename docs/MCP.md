# Reuse Claude MCP registrations

Prefer references to existing Claude stdio MCP servers:

```toml
[jira.mcp]
from = "atlassian"
[gitlab.mcp]
from = "gitlab"
[github.mcp]
from = "github"
```

Discovery reads project `.mcp.json` and user `~/.claude.json`, including
project-scoped registrations. Local scope wins over project, then user scope;
whole server entries replace lower-priority entries, matching
[Claude's precedence](https://code.claude.com/docs/en/mcp#scope-hierarchy-and-precedence).
A single clear provider match is selected automatically; ambiguous matches need
an explicit `from` selection. MEOW stores only the name in ignored local config,
resolving its current command, arguments, and environment when used. Credentials
are not copied into generated config or receipts. Existing explicit references
are retained even when additional registrations appear.

Detection makes no connections. References support stdio registrations;
hosted connectors and OAuth/HTTP sessions are not reusable by the detached CLI.
Explicit command-based configurations remain supported for compatibility.

Inspect setup with `meow native onboard-status --work-dir PATH`.
Override a choice with `meow onboard --work-dir PATH --set 'jira.mcp.from="NAME"'`.
`meow native verify` checks readiness; only a real tool call verifies connection.
