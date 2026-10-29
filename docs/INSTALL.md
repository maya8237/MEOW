# Install, update, and uninstall

MEOW needs Python 3.12+ and Claude Code with authentication set up.

## Install

PowerShell:

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
```

Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
```

The installer uses the newest Python 3.12+ it finds; set `MEOW_PYTHON` to
choose one. It puts MEOW in `~/.meow`, adds `meow` to your PATH, and sets up
its skills in Claude Code. It uses `uv` for the virtual environment and package
installs when `uv` is already installed, and pip otherwise or if `uv` fails.
Open a new terminal or restart Claude Code.

In a terminal, installation then asks about Jira, Confluence and GitLab MCP servers (tokens are shown as typed), Bun's shared
dependency store, and which projects to set up. Every question can be skipped.
Without a terminal, or with `--no-setup`, it only registers MEOW. The
non-interactive switch also works for unattended installation:

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh -s -- --non-interactive
# Or, from a checkout:
sh scripts/install.sh --non-interactive
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1))) -non-interactive
# Or, from a checkout:
./scripts/install.ps1 -non-interactive
```

`--no-setup` (`-no-setup` in PowerShell) skips the questions but still honors
`--mcp` and `--bun`. `--non-interactive` skips the questions too.
The installers read PyPI and pick the newest stable wheel. `--latest-pypi` takes
the newest beta or stable, `--beta` the newest beta, `--version X.Y.Z[bN]` an exact
version (single-dash in PowerShell).
`--mcp atlassian,gitlab` registers user-scope MCP servers without prompting, reading
the servers' own credential variables; see [MCP](MCP.md). `--install-missing` lets `--mcp` pip-install Jira's server. `--bun` enables the
Bun shared store and migrates registered repos. Projects are set up with
`meow onboard --non-interactive --work-dir PATH`.

To rerun only MCP registration after installation or from a boot script, use
`meow mcp atlassian,gitlab`; it does not refresh project configuration.

The switch works even with a terminal attached. Installation still registers
the Claude Code plugin and launcher. Configure projects separately with
`meow onboard --non-interactive --work-dir PATH`; see
[flag-based onboarding](ONBOARDING.md) for optional integration and feature flags.

Open your project in Claude Code and run `/meow:run` directly. First use detects
local project settings and compatible registered MCP servers. Detection starts
no agents, connections, tests, or dependency installs. A task prepares its own
dependencies when needed; provider connections are checked when used.
Use `/meow:onboard` to inspect or override settings and enable optional features.

## Update

```bash
meow --update
```

This installs the newest stable release from PyPI (`meow --update --latest-pypi`, `--beta` or `--version X` select others). See the [CLI guide](CLI.md#common-options)
for runtime handling, update checks, and editable-install migration.

## Uninstall

PowerShell:

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.ps1 | iex
```

Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.sh | sh
```

For unattended removal, pass `--non-interactive` (`-non-interactive` in
PowerShell); it never prompts and keeps `~/.meow` data, project setup, and Bun
settings:

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.sh | sh -s -- --non-interactive
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.ps1))) -non-interactive
```

Otherwise it asks before changing anything. It removes the MEOW release, `meow`/`mw` on
PATH, the Claude Code plugin and skill registrations, scheduled MEOW runs, and
(optionally) `~/.meow` data, Bun shared-store settings, and each project's
`.meow/`, and plugin entries.
