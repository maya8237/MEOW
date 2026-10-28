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

Servers rejected through `disabledMcpjsonServers` in Claude's settings files are
ignored for `.mcp.json` only. MEOW does not use Claude's project-server approval
prompt: `from` is your own choice, and setup prints the registration name and
launcher it bound.

Detection makes no connections. References support stdio registrations;
hosted connectors and OAuth/HTTP sessions are not reusable by the detached CLI.
Explicit command-based configurations remain supported for compatibility.

Inspect setup with `meow native onboard-status --work-dir PATH`.
Override a choice with `meow onboard --work-dir PATH --set 'jira.mcp.from="NAME"'`.
`meow native verify` checks readiness; only a real tool call verifies connection.

## Set up your own servers

MCP servers belong to each user, not the project. Register them once:

```bash
export JIRA_URL=https://acme.atlassian.net JIRA_USERNAME=me@example.com
export JIRA_API_TOKEN=...            # or JIRA_PERSONAL_TOKEN (Server/Data Center)
export GITLAB_PERSONAL_ACCESS_TOKEN=... GITLAB_API_URL=https://gitlab.example.com/api/v4
meow onboard --mcp jira,gitlab   # or the installer's --mcp flag
```

The installer asks the same under `--setup`; `--non-interactive` never asks.
Servers: Jira `uvx mcp-atlassian`, GitLab `npx -y @zereight/mcp-gitlab`;
variables keep the servers' own names. GitHub has no setup yet: register your
own with `claude mcp add --scope user` and MEOW uses it. MEOW registers at
Claude's user scope with `claude mcp add`, skips providers already registered,
and never installs `uvx`, `npx`, or `claude`: a missing launcher is reported.
Tokens are stored by Claude in plain text in `~/.claude.json`; MEOW never prints
or copies them.

Shared project config keeps only project facts (`[jira] project_key`, a site
`url` that is not secret; GitLab's is the site, MEOW adds `/api/v4`). The url
prefills setup, identifies a self-hosted origin, and a registered server that
targets another host logs a warning. Without `[provider.mcp]`, MEOW uses your single
user-scope server for that provider; several matches need `from` in local config,
and none fails with the command to run. Scheduled runs need the same user.
