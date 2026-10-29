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
export CONFLUENCE_URL=https://acme.atlassian.net/wiki   # optional, same server
export CONFLUENCE_USERNAME=me@example.com CONFLUENCE_API_TOKEN=...
                                     # or CONFLUENCE_PERSONAL_TOKEN (Server/Data Center)
export JIRA_SSL_VERIFY=false CONFLUENCE_SSL_VERIFY=false  # optional TLS verification settings
export GITLAB_PERSONAL_ACCESS_TOKEN=... GITLAB_API_URL=https://gitlab.example.com/api/v4
meow mcp atlassian,gitlab         # registration only; safe to rerun
meow onboard --mcp atlassian,gitlab  # registration plus project onboarding
```

The installer asks the same when run in a terminal; `--no-setup` and
`--non-interactive` never ask.
Confluence rides on the Atlassian server; Jira and Confluence are each optional
(either alone is enough). Any `CONFLUENCE_*` variable set above, plus the
optional `JIRA_SSL_VERIFY` and `CONFLUENCE_SSL_VERIFY` settings, is forwarded to
the Atlassian server (a `[confluence] url` in project config prefills
`CONFLUENCE_URL`).
Setup skips an already registered server, so to add Confluence to one, run
`claude mcp remove atlassian --scope user` and rerun setup.
Servers: Jira `mcp-atlassian` (pip), GitLab `npx -y @zereight/mcp-gitlab`;
variables keep the servers' own names. For other servers, register your own
with `claude mcp add --scope user` and MEOW uses it. MEOW registers at
Claude's user scope with `claude mcp add` and skips providers already registered.
If `mcp-atlassian` is missing, MEOW asks (terminal) or needs `--install-missing`
before running `pip install mcp-atlassian` into its own `~/.meow/tools`
environment with the Python running MEOW, then registers that path. It never
installs `npx` or `claude`: a missing one is reported.
Tokens are stored by Claude in plain text in `~/.claude.json`; MEOW never prints
or copies them.

Shared project config keeps only project facts (`[jira] project_key`, a site
`url` that is not secret; GitLab's is the site, MEOW adds `/api/v4`). The url
prefills setup, identifies a self-hosted origin, and a registered server that
targets another host logs a warning. Without `[provider.mcp]`, MEOW uses your single
user-scope server for that provider; several matches need `from` in local config,
and none fails with the command to run. Scheduled runs need the same user.

## Override the setup commands

Change how MEOW registers a server, or add one, in `~/.meow/config.toml` or the
ignored `.meow/config.local.toml` (a committed `.meow/config.toml` is ignored,
since setup writes into your own Claude config):

```toml
[mcp_setup.gitlab]                 # pin the version; other fields keep defaults
args = ["-y", "@zereight/mcp-gitlab@2.1.45"]

[mcp_setup.mytool]                 # a new provider needs a command
command = "npx"
args = ["-y", "my-mcp-package"]
auth = [["MYTOOL_TOKEN"]]          # alternatives of required variables
warmup = []                        # [] skips the one-time download
[mcp_setup.mytool.env]
MYTOOL_HOST = "https://tool.example.com"   # fixed, non-secret settings only
```

Keys: `command`, `args`, `server` (name in Claude), `label`, `auth`, `env`,
`optional` (variables forwarded when set), `warmup`. Secrets always come from the `auth` variables in the environment, never
the file. This changes registration only; to change how a project launches a
server at run time use `[provider.mcp]`.
