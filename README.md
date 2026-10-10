# MEOW

[![CI](https://github.com/maya8237/MEOW/actions/workflows/ci.yml/badge.svg)](https://github.com/maya8237/MEOW/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)](https://www.python.org/downloads/)

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
| Review | `/meow:review ...` or `meow review ...` |
| Integrate CI review | `/meow:integrate-ci-review` |
| Fix lint | `/meow:lint` |
| Queue work | `meow queue "..."` |
| Check or continue | `meow status` or `meow resume RUN_ID` |

See the [CLI guide](docs/CLI.md) for worktrees, background runs, recovery, and
advanced options.

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
