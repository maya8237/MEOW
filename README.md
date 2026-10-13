# MEOW

<p align="center">
  <img src="logo.png" width="160" alt="MEOW pixel cat logo" />
</p>

> Tell MEOW what you want. Get the finished branch.

MEOW is a workflow harness for Claude that plans, implements, tests, reviews,
and delivers software changes in isolated worktrees.

## Install

### PowerShell

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
```

### Bash

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
```

Installation offers Bun shared dependency storage (default **Yes**) and
automatically migrates registered and selected Bun projects. MEOW prepares
missing Node dependencies in SDK and native worktrees with or without this
option. See [worktree setup and Bun storage](docs/INTEGRATIONS.md#new-worktree-setup).

## Run

Claude Code:

```text
/meow:run Add CSV export
```

Terminal or CI:

```bash
meow run "Add CSV export" --name csv-export
```

Projects are set up automatically on first use when a config-dependent command
runs. Use `/meow:onboard` for integrations and optional features, or
`/meow:migration` for a legacy MEOW layout.

## Common commands

| Need | Command |
| --- | --- |
| Plan only | `/meow:plan ...` or `meow plan ...` |
| Several related changes at once | `/meow:orchestrate ...` or `meow orchestrate "..."` |
| Review | `/meow:review ...` or `meow review ...` |
| Integrate CI review | `/meow:integrate-ci-review` |
| Fix lint | `/meow:lint` |
| Queue work | `meow queue "..."` |
| Check or continue | `meow status` or `meow resume RUN_ID` |
| Browse definitions or manage command sessions | `meow ui` |

`meow ui` opens a local, bundled app for workflows, agents, skills, and saved
command sessions. Sessions can run through the MEOW CLI or Claude Code. Feature
runs default to unattended execution in an isolated worktree; successful,
verified unattended runs retain MEOW's existing commit-and-push delivery
policy. Local hosting does not disable delivery. Claude permission checks stay
enabled by default, with permission bypass available only as an explicit,
labeled opt-in.

Recipes and each attempt's output are saved under the selected repository's
`.meow/ui/sessions/`. Edit changes the next attempt, rerun starts fresh, duplicate
creates a draft, and resume continues a stopped resumable execution. Existing
MEOW runs also appear when their repositories are registered; historical output
is available only when a background log was recorded. See the [CLI guide](docs/CLI.md#selecting-and-editing-workflows)
for permission, archive, import, and resume details.

`meow orchestrate "Add SSO login and an admin audit log"` splits an objective
into tasks, runs them in parallel, merges them into one branch, reviews it as
a whole, and opens a single pull request.

See the [CLI guide](docs/CLI.md) for worktrees, background runs, recovery,
orchestration, and advanced options.

## Configuration and integrations

- `.meow/config.toml` — shareable project settings.
- `.meow/config.local.toml` — ignored machine- or user-specific settings.
- `~/.meow/config.toml` — user fallback settings.

MEOW supports Jira, GitLab, GitHub, configured tests and builds, browser
tester checks, Claude Code hooks, and scheduled Jira runs. See the
[integration guide](docs/INTEGRATIONS.md).

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [CLI guide](docs/CLI.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Project instructions](AGENTS.md)
- [Contributing](CONTRIBUTING.md)
- [Roadmap](docs/ROADMAP.md)
- [Changelog](CHANGELOG.md)
- [License](LICENSE)
